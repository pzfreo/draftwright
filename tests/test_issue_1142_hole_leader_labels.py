"""Regression coverage for semantic hole-callout leader labels (#1142)."""

from types import SimpleNamespace

from build123d import Align, Box, Cylinder, Pos
from build123d_drafting.helpers import draft_preset

from draftwright import Sheet, build_drawing
from draftwright.annotations.from_model import callout_from_spec
from draftwright.linting import lint_drawing
from draftwright.model import hole


def _plate():
    part = Box(80, 50, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
    for x in (-30, 30):
        for y in (-15, 15):
            part -= Pos(x, y, 0) * Cylinder(3, 10)
    return part - Cylinder(10, 10)


def _hole_leaders(drawing):
    return [ann for name, ann in drawing.iter_annotations() if name.startswith("hc_")]


def test_callout_semantic_label_covers_the_rendered_compound_grammar():
    callout = callout_from_spec(
        {
            "diameter": 8,
            "count": 4,
            "through": False,
            "depth": 12,
            "cbore_dia": 14,
            "cbore_depth": 2,
            "csink_dia": 16,
            "csink_angle": 90,
            "suffix": "EQ SP ON ⌀50 BC",
            "tolerance": None,
        },
        draft_preset(decimal_precision=1),
        count=4,
    )

    assert callout is not None
    assert callout.label == "4× ⌀8 ↧ 12 ⌴ ⌀14 ↧ 2 ⌵ ⌀16 × 90° EQ SP ON ⌀50 BC"


def test_automatic_hole_leaders_expose_nonempty_semantic_labels():
    drawing = build_drawing(_plate(), page="A4", scale=0.5)
    leaders = _hole_leaders(drawing)

    assert len(leaders) == 2
    labels_by_diameter = {leader.covers_diameters[0]: leader.label for leader in leaders}
    assert labels_by_diameter[6.0].startswith("4× ⌀6")
    assert labels_by_diameter[20.0].startswith("⌀20")
    assert all(leader.label_bbox is not None for leader in leaders)


def test_declared_pattern_and_bore_have_semantic_labels_and_clean_centerline_lint():
    sheet = Sheet(_plate(), page="A4", scale=0.5).authored_dimensions()
    member = hole(diameter=6, depth=10, through=True, at=(-30, -15, 0), axis="z")
    pattern = sheet.pattern(
        member,
        kind="other",
        count=4,
        members=((-30, -15, 0), (-30, 15, 0), (30, -15, 0), (30, 15, 0)),
    )
    bore = sheet.hole(diameter=20, at=(0, 0, 0), axis="z")
    sheet.envelope()
    sheet.dimension(pattern, "bore.diameter")
    sheet.dimension(bore, "bore.diameter")

    drawing = sheet.build()
    leaders = _hole_leaders(drawing)
    assert {leader.label for leader in leaders} == {"4× ⌀6 THRU", "⌀20 THRU"}
    assert sum(name.startswith("m_cm") for name, _ in drawing.iter_annotations()) == 5
    assert not [issue for issue in drawing.lint() if issue.code == "label_centerline_overlap"]


def test_centerline_overlap_uses_text_bbox_not_full_leader_footprint():
    broad_box = SimpleNamespace(
        min=SimpleNamespace(X=0.0, Y=0.0), max=SimpleNamespace(X=30.0, Y=24.0)
    )
    leader = SimpleNamespace(
        label="4× ⌀6 THRU",
        label_bbox=(20.0, 20.0, 30.0, 24.0),
        bounding_box=lambda: broad_box,
    )
    centerline = SimpleNamespace(
        is_centerline=True,
        segments=(((5.0, 0.0), (5.0, 10.0)),),
    )

    assert not [
        issue
        for issue in lint_drawing([leader, centerline])
        if issue.code == "label_centerline_overlap"
    ]

    # Prove the public text box is load-bearing: removing it makes lint fall back to the
    # broad leader footprint, which intersects the centreline and must warn.
    leader.label_bbox = None
    assert [
        issue
        for issue in lint_drawing([leader, centerline])
        if issue.code == "label_centerline_overlap"
    ]
