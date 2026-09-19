"""Quiddity 0.3.0 gussets cross Draftwright's recognition boundary honestly."""

from types import SimpleNamespace

import pytest
from build123d import Align, Box, Location, Plane, Polygon, extrude
from quiddity import GussetRib

from draftwright import Sheet, build_drawing
from draftwright.linting.gusset_rib_coverage import gusset_rib_requirement_outcomes
from draftwright.model import gusset_rib
from draftwright.registry import AnnotationRegistry
from draftwright.sheet_emit import emit_sheet_script


def _mirrored_gusset_bracket():
    on_plane = (Align.CENTER, Align.CENTER, Align.MIN)
    plate = Box(80, 50, 8, align=on_plane)
    flange = Box(80, 8, 48, align=on_plane).moved(Location((0, 21, 0)))
    rib = extrude(
        Plane.YZ * Polygon((17, 8), (-5, 8), (17, 34), align=None),
        amount=6,
    )
    return plate + flange + rib.moved(Location((-26, 0, 0))) + rib.moved(Location((32, 0, 0)))


def _array_gusset_bracket():
    on_plane = (Align.CENTER, Align.CENTER, Align.MIN)
    part = Box(100, 50, 8, align=on_plane)
    part += Box(100, 8, 48, align=on_plane).moved(Location((0, 21, 0)))
    rib = extrude(Plane.YZ * Polygon((17, 8), (-5, 8), (17, 34), align=None), amount=6)
    for position in (-30, -5, 20):
        part += rib.moved(Location((position, 0, 0)))
    return part


def test_gusset_ribs_and_mirror_relation_reach_same_run_consumer_policy() -> None:
    drawing = build_drawing(_mirrored_gusset_bracket())
    result = drawing.recognition()
    evidence = drawing.recognition_evidence()
    ownership = drawing.recognition_ownership()

    assert result is not None and evidence is not None and ownership is not None
    assert [rib.thickness_bounds for rib in result.gusset_ribs] == [
        (-32.0, -26.0),
        (26.0, 32.0),
    ]
    assert all(
        rib.thickness_axis == "x"
        and rib.supports == (("y", 17.0), ("z", 8.0))
        and rib.legs == (22.0, 26.0)
        and rib.directions == (-1, 1)
        for rib in result.gusset_ribs
    )
    (pattern,) = result.gusset_rib_patterns
    assert all(member is rib for member, rib in zip(pattern.ribs, result.gusset_ribs, strict=True))
    assert pattern.mirror_plane == ("x", 0.0)

    (feature,) = [feature for feature in drawing.model().features if feature.kind == "gusset_rib"]
    assert feature.pattern == "mirror"
    assert feature.member_bounds == ((-32.0, -26.0), (26.0, 32.0))
    assert [parameter.parameter_id for parameter in feature.parameters()] == [
        "gusset_thickness.length",
        "gusset_leg.length.y",
        "gusset_leg.length.z",
        "gusset_spacing.length",
        "gusset_location.length",
    ]

    gusset_refs = tuple(
        ref for ref in evidence.features if evidence.family(ref).startswith("gusset_rib")
    )
    # Derived patterns relate physical occurrences and do not mint a third evidence occurrence.
    assert len(gusset_refs) == 2
    bindings = [binding for binding in ownership.bindings if binding.occurrence in gusset_refs]
    assert len(bindings) == 2
    assert all(binding.feature is feature for binding in bindings)
    assert all(binding.disposition == "absorbed" for binding in bindings)
    assert [binding.member_index for binding in bindings] == [0, 1]

    name = next(name for name in drawing.annotations() if name.startswith("m_gusset_rib_"))
    annotation = drawing.registry.named(name)
    assert annotation.label == (
        "2× GUSSET 6 THK · LEGS 22 × 26 · 58 C/C MIRROR · MIRROR PLANE 40 FROM X MIN"
    )
    assert annotation.tip == drawing.at("side", *feature.leader_anchor)[:2]
    assert {identity.parameter for identity in drawing.registry.measurement_of(name)} == {
        "gusset_thickness.length",
        "gusset_leg.length.y",
        "gusset_leg.length.z",
        "gusset_spacing.length",
        "gusset_location.length",
    }
    assert not [issue for issue in drawing.lint() if "gusset" in issue.code]
    completeness = drawing.lint_summary()["quality"]["completeness"]
    assert completeness["by_family"]["gusset_ribs"] == 8

    declared = Sheet(_mirrored_gusset_bracket())
    declared.auto_dimensions()
    declared.gusset_rib(
        axis=feature.axis,
        supports=feature.supports,
        legs=feature.legs,
        directions=feature.directions,
        member_bounds=feature.member_bounds,
        pattern=feature.pattern,
        mirror_plane=feature.mirror_plane,
        datum=feature.datum,
    )
    assert declared.model().features == [feature]

    script = emit_sheet_script(
        drawing.model(), "part = PART", "gusset", title="Gusset", number="1705"
    )
    line = next(line for line in script.splitlines() if "sheet.gusset_rib(" in line)
    expression = line.split("#", 1)[0].split(" = ", 1)[1].strip()
    replay = Sheet(_mirrored_gusset_bracket())
    replay.auto_dimensions()
    eval(expression, {"sheet": replay})
    assert [item for item in replay.model().features if item.kind == "gusset_rib"] == [feature]


def test_linear_gusset_array_keeps_provider_pitch_and_member_ownership() -> None:
    drawing = build_drawing(_array_gusset_bracket())
    result = drawing.recognition()
    ownership = drawing.recognition_ownership()
    evidence = drawing.recognition_evidence()
    assert result is not None and ownership is not None and evidence is not None

    (pattern,) = result.gusset_rib_patterns
    assert pattern.pitch == 25.0
    assert all(member is rib for member, rib in zip(pattern.ribs, result.gusset_ribs, strict=True))
    (feature,) = [feature for feature in drawing.model().features if feature.kind == "gusset_rib"]
    assert feature.pattern == "linear"
    assert feature.pitch == 25.0
    assert feature.member_bounds == ((-36.0, -30.0), (-11.0, -5.0), (14.0, 20.0))

    refs = tuple(ref for ref in evidence.features if evidence.family(ref) == "gusset_ribs")
    assert len(refs) == 3
    assert all(ownership.binding_for(ref).feature is feature for ref in refs)
    name = next(name for name in drawing.annotations() if name.startswith("m_gusset_rib_"))
    assert drawing.registry.named(name).label == (
        "3× GUSSET 6 THK · LEGS 22 × 26 · 25 PITCH · FIRST CL 17 FROM X MIN"
    )
    completeness = drawing.lint_summary()["quality"]["completeness"]
    assert completeness["by_family"]["gusset_ribs"] == 11


def test_declared_gusset_patterns_reject_false_relationships() -> None:
    common = dict(
        axis="x",
        supports=(("y", 17), ("z", 8)),
        legs=(22, 26),
        directions=(-1, 1),
        datum=-40,
    )
    with pytest.raises(ValueError, match="pattern facts are inconsistent"):
        gusset_rib(
            **common,
            member_bounds=((-3, 3), (7, 13), (24, 30)),
            pattern="linear",
            pitch=10,
        )
    with pytest.raises(ValueError, match="pattern facts are inconsistent"):
        gusset_rib(
            **common,
            member_bounds=((-3, 3), (7, 13)),
            pattern="mirror",
            mirror_plane=("x", 0),
        )
    with pytest.raises(ValueError, match="ordered"):
        gusset_rib(
            **common,
            member_bounds=((7, 13), (-3, 3)),
            pattern="mirror",
            mirror_plane=("x", 5),
        )


def test_declared_completeness_does_not_reuse_one_member_for_two_occurrences() -> None:
    first = GussetRib("x", (-3.0, 3.0), (("y", 17.0), ("z", 8.0)), (22.0, 26.0), (-1, 1), (1.0,))
    second = GussetRib("x", (-3.0, 3.0), (("y", 17.0), ("z", 8.0)), (22.0, 26.0), (-1, 1), (2.0,))
    records = {"first": first, "second": second}
    evidence = SimpleNamespace(
        features=("first", "second"),
        family=lambda _ref: "gusset_ribs",
        record=records.__getitem__,
    )
    feature = gusset_rib(
        axis="x",
        supports=first.supports,
        legs=first.legs,
        directions=first.directions,
        member_bounds=(first.thickness_bounds,),
        datum=-40,
    )

    outcomes = gusset_rib_requirement_outcomes(
        SimpleNamespace(),
        (feature,),
        AnnotationRegistry(),
        (),
        evidence=evidence,
        ownership=None,
    )

    assert [(outcome.parameter_id, outcome.state) for outcome in outcomes] == [
        ("gusset_thickness.length", "unverifiable"),
        ("gusset_thickness.length", "unverifiable"),
    ]
