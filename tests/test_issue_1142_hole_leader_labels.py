"""Regression coverage for semantic hole-callout leader labels (#1142)."""

import math
from types import SimpleNamespace

import pytest
from build123d import Align, Box, Cylinder, Pos
from build123d_drafting.helpers import HoleCallout, draft_preset

from draftwright import Sheet, build_drawing
from draftwright.annotations.from_model import callout_from_spec
from draftwright.fits import fit_class
from draftwright.linting import lint_drawing, lint_feature_coverage
from draftwright.model import hole


def _plate():
    cutter_align = (Align.CENTER, Align.CENTER, Align.MIN)
    part = Box(80, 50, 10, align=cutter_align)
    for x in (-30, 30):
        for y in (-15, 15):
            part -= Pos(x, y, 0) * Cylinder(3, 10, align=cutter_align)
    return part - Cylinder(10, 10, align=cutter_align)


def _hole_leaders(drawing):
    return [ann for name, ann in drawing.iter_annotations() if name.startswith("hc_")]


def _spec(**overrides):
    spec = {
        "diameter": 8,
        "count": None,
        "through": True,
        "depth": None,
        "cbore_dia": None,
        "cbore_depth": None,
        "csink_dia": None,
        "csink_angle": None,
        "suffix": None,
        "tolerance": None,
    }
    spec.update(overrides)
    return spec


@pytest.mark.parametrize(
    ("spec", "count", "expected"),
    [
        (_spec(), None, "⌀8 THRU"),
        (_spec(through=False, depth=12), None, "⌀8 ↧ 12"),
        (_spec(tolerance=0.1), None, "⌀8 ±0.1 THRU"),
        (_spec(tolerance=(0.0, 0.2)), None, "⌀8 +0.2 -0.0 THRU"),
        (_spec(tolerance=fit_class("H7", 8)), None, "⌀8 H7 THRU"),
        (_spec(suffix="M8x1.25"), None, "⌀8 THRU M8x1.25"),
        (_spec(count=9, suffix="(3×3)"), 9, "9× ⌀8 THRU (3×3)"),
        (_spec(count=5, suffix="EQ SP ON ø50 BC"), 5, "5× ⌀8 THRU EQ SP ON ø50 BC"),
        (_spec(suffix="DOUBLE-D 6 A/F"), None, "⌀8 THRU DOUBLE-D 6 A/F"),
    ],
    ids=[
        "through",
        "blind",
        "symmetric-tolerance",
        "limit-tolerance",
        "fit",
        "thread",
        "grid",
        "bolt-circle",
        "double-d",
    ],
)
def test_callout_semantic_label_covers_rendered_branches(spec, count, expected):
    callout = callout_from_spec(spec, draft_preset(decimal_precision=1), count=count)

    assert callout is not None
    assert callout.label == expected


def test_callout_semantic_label_covers_the_rendered_compound_grammar():
    callout = callout_from_spec(
        _spec(
            count=4,
            through=False,
            depth=12,
            cbore_dia=14,
            cbore_depth=2,
            csink_dia=16,
            csink_angle=90,
            suffix="EQ SP ON ø50 BC",
        ),
        draft_preset(decimal_precision=1),
        count=4,
    )

    assert callout is not None
    assert callout.label == "4× ⌀8 ↧ 12 ⌴ ⌀14 ↧ 2 ⌵ ⌀16 × 90° EQ SP ON ø50 BC"


def test_public_build_rejects_a_labelless_geometric_callout(monkeypatch):
    """The semantic-label guard is load-bearing on the public compiler path."""
    original_setattr = HoleCallout.__setattr__

    def discard_semantic_label(callout, name, value):
        # Preserve the upstream geometry-only ``label=""`` assignment, but mutate away
        # Draftwright's semantic restoration to prove the leader seam fails loudly.
        if name == "label" and value:
            return
        original_setattr(callout, name, value)

    monkeypatch.setattr(HoleCallout, "__setattr__", discard_semantic_label)
    with pytest.raises(
        ValueError, match="a rendered hole callout must carry a non-empty semantic label"
    ):
        build_drawing(_plate(), page="A4", scale=0.5)


def test_automatic_hole_leaders_expose_nonempty_semantic_labels():
    drawing = build_drawing(_plate(), page="A4", scale=0.5)
    leaders = _hole_leaders(drawing)

    assert len(leaders) == 2
    labels_by_diameter = {leader.covers_diameters[0]: leader.label for leader in leaders}
    assert labels_by_diameter == {6.0: "4× ⌀6 THRU", 20.0: "⌀20 THRU"}
    assert all(leader.label_bbox is not None for leader in leaders)


def test_declared_pattern_and_bore_have_semantic_labels_and_clean_centerline_lint():
    sheet = Sheet(_plate(), page="A4", scale=0.5).authored_dimensions()
    member = hole(diameter=6, through=True, at=(-30, -15, 0), axis="z")
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


def test_structured_callout_label_does_not_claim_its_bolt_circle_as_a_feature():
    part = Cylinder(30, 10)
    for index in range(5):
        angle = 2 * math.pi * index / 5
        part -= Pos(20 * math.cos(angle), 20 * math.sin(angle), 0) * Cylinder(3, 10)
    callout = SimpleNamespace(
        label="5× ⌀6 THRU EQ SP ON ø60 BC",
        covers_diameters=(6.0,),
        covers_count=5,
    )

    issues = lint_feature_coverage(part, [callout], assembly=False)
    assert any(
        issue.code == "feature_not_dimensioned" and "ø60" in issue.message for issue in issues
    )


def test_structured_callout_label_does_not_waive_a_count_shortfall():
    cutter_align = (Align.CENTER, Align.CENTER, Align.MIN)
    part = Box(50, 50, 10, align=cutter_align)
    for x in (-15, 15):
        for y in (-15, 15):
            part -= Pos(x, y, 0) * Cylinder(5, 10, align=cutter_align)
    incomplete_callout = SimpleNamespace(
        label="2× ⌀10 THRU",
        covers_diameters=(10.0,),
        covers_count=2,
    )

    issues = lint_feature_coverage(part, [incomplete_callout], assembly=False)
    assert any(
        issue.code == "feature_count_mismatch" and "ø10" in issue.message for issue in issues
    )


def test_structured_bolt_circle_label_does_not_suppress_an_unrelated_boss_diameter():
    """Renderer dedup must consume structured coverage, not BCD suffix text (ADR 0017)."""
    cutter_align = (Align.CENTER, Align.CENTER, Align.MIN)
    part = Box(200, 100, 10, align=cutter_align)
    boss_solid = Pos(-60, 0, 10) * Cylinder(30, 5, align=cutter_align)
    part += boss_solid
    members = ((80, 0, 0), (50, 30, 0), (20, 0, 0), (50, -30, 0))
    for x, y, z in members:
        part -= Pos(x, y, z) * Cylinder(3, 10, align=cutter_align)

    sheet = Sheet(part, page="A3", scale=0.5).auto_dimensions()
    member = hole(diameter=6, through=True, at=members[0], axis="z")
    sheet.pattern(
        member,
        kind="bolt_circle",
        count=4,
        bcd=60,
        at=(50, 0, 5),
        members=members,
    )
    sheet.boss(boss_solid)

    drawing = sheet.build()
    pattern_callout = drawing.get_annotation("hc_plan0")
    assert pattern_callout.label == "4× ⌀6 THRU EQ SP ON ø60 BC"
    assert pattern_callout.covers_diameters == (6.0,)
    boss_callout = drawing.get_annotation("m_bossdia_z0")
    assert boss_callout.label == "ø60"
    boss_feature = next(feature for feature in drawing.model().features if feature.kind == "boss")
    assert "m_bossdia_z0" in drawing.annotations_of(boss_feature)
    assert not [
        issue
        for issue in drawing.lint()
        if issue.code.startswith("declared_") or issue.code == "feature_not_dimensioned"
    ]
