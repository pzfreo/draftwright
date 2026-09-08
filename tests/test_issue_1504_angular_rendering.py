"""Angular placement must preserve a true projected angle and expose missing authority."""

import json
from copy import copy
from math import cos, radians, sin
from types import SimpleNamespace

import pytest
from build123d import Axis, Compound, Edge, GeomType, Location, Polygon, Rot, Vertex, extrude

from draftwright import Sheet, build_drawing
from draftwright.annotations.angular import AngularInk
from draftwright.linting.angular import lint_angular_geometry
from draftwright.model import AngularReference


def _angle_sheet(angle=60, rotation=(0, 0, 0), *, sector="minor", **options):
    end = (30 * cos(radians(angle)), 30 * sin(radians(angle)))
    part = extrude(Polygon((0, 0), (30, 0), end, align=None), amount=3)
    transform = Rot(*rotation)
    points = ((0, 0, 3), (15, 0, 3), (end[0] / 2, end[1] / 2, 3))
    vertex, first, second = [tuple((transform * Vertex(*point)).center()) for point in points]
    reference = AngularReference(vertex, first, second, sector=sector)
    part = transform * part
    sheet = Sheet(part, **options)
    sheet.measured_dimension(
        kind="angular",
        value=angle,
        label=f"{angle}°",
        dominant_axis="z",
        ref_pts=(),
        angular_reference=reference,
    )
    sheet.authored_dimensions()
    return sheet, part, reference


def _angle_annotation(drawing):
    annotations = [
        (name, item)
        for name, item in drawing.iter_annotations()
        if hasattr(item, "measured_angle")
    ]
    assert len(annotations) == 1, "one angular requirement must reach one visible mark"
    return annotations[0]


@pytest.fixture(scope="module")
def drawing_draft():
    sheet, _, _ = _angle_sheet()
    return sheet.build().draft


@pytest.mark.parametrize(
    ("rotation", "view"), [((0, 0, 0), "plan"), ((0, 90, 0), "side"), ((90, 0, 0), "front")]
)
@pytest.mark.parametrize("angle", (60, 120))
@pytest.mark.parametrize("scale", (1, 2))
@pytest.mark.parametrize("sector", ("minor", "opposite"))
def test_declared_angles_use_true_views_at_each_scale(rotation, view, angle, scale, sector):
    sheet, _, reference = _angle_sheet(angle, rotation, scale=scale, sector=sector)
    drawing = sheet.build()
    name, annotation = _angle_annotation(drawing)
    assert drawing.view_of(name) == view
    assert annotation.measured_angle == pytest.approx(angle)
    assert getattr(annotation, "measured_length", None) is None
    assert annotation.angular_points[1] == pytest.approx(drawing.at(view, *reference.vertex)[:2])
    assert any(edge.geom_type == GeomType.CIRCLE for edge in annotation.edges())
    assert not [
        issue
        for issue in drawing.lint(physical=False)
        if issue.code
        in {
            "angular_geometry_mismatch",
            "angular_label_vs_geometry",
            "label_vs_measured",
            "dimension_kind_unsupported",
            "annotation_out_of_bounds",
        }
    ]
    assert not [issue for issue in drawing.lint() if issue.code.startswith("angular_support_")]


def test_angular_intent_enters_the_shared_corridor_trace(tmp_path):
    sheet, part, _ = _angle_sheet()
    output = tmp_path / "angular"
    drawing = build_drawing(part, model=sheet.model(), scale=1, out=str(output), trace=True)
    name, _ = _angle_annotation(drawing)
    data = json.loads(output.with_suffix(".trace.json").read_text())
    assert any(
        candidate["name"] == name for solve in data["solves"] for candidate in solve["candidates"]
    )
    fidelity = drawing.lint_summary()["quality"]["fidelity"]
    assert fidelity["by_code"].get("angular_support_unverifiable", 0) == 0
    assert fidelity["score"] == 1


def test_compaction_only_considers_the_current_candidate_set(monkeypatch):
    from draftwright.annotations import _common

    place = _common.place_strip_candidates
    checked = []

    def outside_batch():
        pytest.fail("a retry must not compact an annotation outside its candidate set")

    def with_shared_options(drawing, strip, view, axis, candidates, tier, **kwargs):
        # Corridor retries share option maps with the original batch, while
        # their candidate list contains only the unplaced remainder.
        assert "already_placed_angle" not in {name for name, _build in candidates}
        kwargs["compact_candidates"] = {
            **(kwargs.get("compact_candidates") or {}),
            "already_placed_angle": outside_batch,
        }
        checked.append(bool(candidates))
        return place(drawing, strip, view, axis, candidates, tier, **kwargs)

    monkeypatch.setattr(_common, "place_strip_candidates", with_shared_options)
    sheet, _, _ = _angle_sheet()
    _angle_annotation(sheet.build())
    assert any(checked)


def test_insufficient_arc_clearance_is_a_reported_drop_not_a_render_exception():
    sheet, part, _ = _angle_sheet(0.25, scale=1, scale_policy="permissive")
    assert min(part.bounding_box().size) > 0.1, "do not hit the projected-geometry guard"
    drawing = sheet.build()
    assert not [item for _, item in drawing.iter_annotations() if hasattr(item, "measured_angle")]
    assert [issue for issue in drawing.lint(physical=False) if issue.code == "pmi_dropped"]
    assert not [
        issue
        for issue in drawing.lint(physical=False)
        if issue.code == "dimension_kind_unsupported"
    ]


@pytest.mark.parametrize("wrong_label", ("120°", "60 mm", "60"))
def test_angular_lint_catches_a_changed_label_without_using_the_renderer_value(wrong_label):
    sheet, _, _ = _angle_sheet()
    drawing = sheet.build()
    _, annotation = _angle_annotation(drawing)
    assert not [
        issue
        for issue in drawing.lint(physical=False)
        if issue.code == "angular_label_vs_geometry"
    ]
    annotation.label = wrong_label
    findings = drawing.lint(physical=False)
    assert [issue for issue in findings if issue.code == "angular_label_vs_geometry"]
    assert not [issue for issue in findings if issue.code == "label_vs_measured"]
    assert drawing.lint_summary()["quality"]["fidelity"]["by_code"]["angular_label_vs_geometry"]


def test_moved_angular_metadata_tracks_its_visible_geometry():
    sheet, _, _ = _angle_sheet()
    drawing = sheet.build()
    _, annotation = _angle_annotation(drawing)
    original = annotation.label_bbox
    annotation.location = Location((7, 11, 0))
    assert annotation.label_bbox == pytest.approx(
        tuple(v + delta for v, delta in zip(original, (7, 11, 7, 11), strict=True))
    )
    assert not [
        issue for issue in drawing.lint(physical=False) if issue.code.startswith("angular_")
    ]


@pytest.mark.parametrize("sector", ("minor", "opposite"))
def test_lint_rejects_visible_ink_rotated_away_from_its_reference_sector(sector):
    sheet, _, _ = _angle_sheet(sector=sector)
    drawing = sheet.build()
    _, annotation = _angle_annotation(drawing)
    assert not [
        issue
        for issue in drawing.lint(physical=False)
        if issue.code == "angular_geometry_mismatch"
    ]
    vertex = annotation.angular_points[1]
    old_box = annotation.bounding_box()
    wrong_sector = annotation.rotate(Axis((*vertex, 0), (0, 0, 1)), 180)
    assert wrong_sector.bounding_box().center() != old_box.center()
    # Deliberately replace only visible ink. Trusting the renderer's value or
    # reference metadata alone would still report a correct 60-degree angle.
    annotation.wrapped = wrong_sector.wrapped
    assert annotation.measured_angle == pytest.approx(60)
    assert [
        issue
        for issue in drawing.lint(physical=False)
        if issue.code == "angular_geometry_mismatch"
    ]
    assert drawing.lint_summary()["quality"]["fidelity"]["by_code"]["angular_geometry_mismatch"]


@pytest.mark.parametrize("angle", (12.85062, 60, 120, 175))
@pytest.mark.parametrize("extra_radius", (0, 15))
@pytest.mark.parametrize("rotation", (0, 90, 180, 270))
@pytest.mark.parametrize("sector", ("minor", "opposite"))
def test_radius_dependent_footprint_contains_real_ink(
    angle, extra_radius, rotation, drawing_draft, sector
):
    # Probe the primitive, not the planner's prediction, so an under-sized
    # footprint cannot make both sides of this check agree by construction.
    ink = AngularInk(
        (0, 0),
        (15 * cos(radians(rotation)), 15 * sin(radians(rotation))),
        (15 * cos(radians(angle + rotation)), 15 * sin(radians(angle + rotation))),
        f"{angle}°",
        drawing_draft,
        sector=sector,
    )
    radius = ink.minimum_radius + extra_radius
    annotation = ink.build(radius)
    predicted = ink.footprint(radius)
    actual = annotation.bounding_box()
    assert predicted[0] <= actual.min.X and predicted[1] <= actual.min.Y
    assert predicted[2] >= actual.max.X and predicted[3] >= actual.max.Y
    assert annotation.label == f"{angle}°"
    assert lint_angular_geometry(annotation, angle) == []


@pytest.mark.parametrize("missing", ("arrows", "extensions"))
def test_lint_requires_visible_arrows_and_extensions(missing, drawing_draft):
    ink = AngularInk((0, 0), (15, 0), (7.5, 15 * sin(radians(60))), "60°", drawing_draft)
    annotation = ink.build(ink.minimum_radius)
    assert lint_angular_geometry(annotation, 60) == []
    if missing == "arrows":
        children = [
            Edge.make_circle(ink.minimum_radius, start_angle=10, end_angle=20),
            Edge.make_circle(ink.minimum_radius, start_angle=40, end_angle=50),
            annotation.children[2],
        ]
    else:
        children = [annotation.children[0], annotation.children[1], annotation.children[3]]
    original_edges = len(annotation.edges())
    annotation.wrapped = Compound(children=children).wrapped
    assert len(annotation.edges()) < original_edges
    assert annotation.measured_angle == pytest.approx(60)
    assert [
        finding
        for finding in lint_angular_geometry(annotation, 60)
        if finding.code == "angular_geometry_mismatch"
    ]


@pytest.mark.parametrize("line_width", (0.1, 0.25, 0.35, 0.5, 1))
def test_angular_lint_accepts_the_actual_extension_stroke_width(line_width, drawing_draft):
    draft = copy(drawing_draft)
    draft.line_width = line_width
    ink = AngularInk((0, 0), (15, 0), (7.5, 15 * sin(radians(60))), "60°", draft)
    annotation = ink.build(ink.minimum_radius)
    assert annotation.edges()
    assert lint_angular_geometry(annotation, 60) == []


def test_reversing_witness_order_preserves_ink_and_independent_angular_reading(drawing_draft):
    first, second = (15, 0), (7.5, 15 * sin(radians(60)))
    forward = AngularInk((0, 0), first, second, "60°", drawing_draft)
    reverse = AngularInk((0, 0), second, first, "60°", drawing_draft)
    forward_ink, reverse_ink = [ink.build(ink.minimum_radius) for ink in (forward, reverse)]
    assert reverse_ink.label_bbox == pytest.approx(forward_ink.label_bbox)
    assert reverse_ink.measured_angle == pytest.approx(forward_ink.measured_angle)
    swapped_metadata = SimpleNamespace(
        label="60°",
        angular_points=forward_ink.angular_points[::-1],
        edges=forward_ink.edges,
        vertices=forward_ink.vertices,
    )
    assert lint_angular_geometry(reverse_ink, 60) == []
    assert lint_angular_geometry(swapped_metadata, 60) == []


@pytest.mark.parametrize(
    "points",
    [
        ((float("nan"), 0), (0, 0), (0, 1)),
        ((0, 0), (0, 0), (0, 1)),
        ((1, 0), (0, 0), (-1, 0)),
        ((1, 0, 0), (0, 0, 0), (0, 1, 0)),
    ],
)
def test_unusable_angular_witnesses_are_findings_not_clean_or_exceptions(points):
    item = SimpleNamespace(label="90°", angular_points=points)
    assert [
        finding
        for finding in lint_angular_geometry(item, 90)
        if finding.code == "angular_geometry_mismatch"
    ]


def test_unreadable_angular_ink_is_a_finding():
    def unreadable():
        raise RuntimeError("circular geometry unavailable")

    item = SimpleNamespace(label="90°", angular_points=((1, 0), (0, 0), (0, 1)), edges=unreadable)
    findings = lint_angular_geometry(item, 90)
    assert len(findings) == 1 and findings[0].code == "angular_geometry_mismatch"
    assert "circular geometry unavailable" in findings[0].message


@pytest.mark.parametrize(
    ("angle", "label", "displayed", "matches"),
    [(60.04, "60.00°", 60.0, False), (1.04, "1.0°", 1.0, True), (60.04, "60.04°", 60.04, True)],
)
def test_angular_label_check_respects_the_displayed_precision(
    angle, label, displayed, matches, drawing_draft
):
    ink = AngularInk(
        (0, 0), (15, 0), (15 * cos(radians(angle)), 15 * sin(radians(angle))), label, drawing_draft
    )
    annotation = ink.build(ink.minimum_radius)
    assert annotation.measured_angle == pytest.approx(angle)
    incorrect = [
        finding
        for finding in lint_angular_geometry(annotation, displayed)
        if finding.code == "angular_label_vs_geometry"
    ]
    assert bool(incorrect) is not matches
