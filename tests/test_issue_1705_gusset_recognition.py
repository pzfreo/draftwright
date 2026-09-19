"""Quiddity 0.3.0 gussets cross Draftwright's recognition boundary honestly."""

from build123d import Align, Box, Location, Plane, Polygon, extrude

from draftwright import build_drawing


def _mirrored_gusset_bracket():
    on_plane = (Align.CENTER, Align.CENTER, Align.MIN)
    plate = Box(80, 50, 8, align=on_plane)
    flange = Box(80, 8, 48, align=on_plane).moved(Location((0, 21, 0)))
    rib = extrude(
        Plane.YZ * Polygon((17, 8), (-5, 8), (17, 34), align=None),
        amount=6,
    )
    return plate + flange + rib.moved(Location((-26, 0, 0))) + rib.moved(Location((32, 0, 0)))


def test_gusset_ribs_and_mirror_relation_reach_same_run_consumer_policy() -> None:
    drawing = build_drawing(_mirrored_gusset_bracket(), auto_dims=False)
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

    gusset_refs = tuple(
        ref for ref in evidence.features if evidence.family(ref).startswith("gusset_rib")
    )
    # Derived patterns relate physical occurrences and do not mint a third evidence occurrence.
    assert len(gusset_refs) == 2
    for ref in gusset_refs:
        outcome = ownership.policy_for(ref)
        assert outcome is not None
        assert outcome.disposition == "deferred"
        assert outcome.reason_code == "consumer_semantics_deferred"
        assert outcome.tracking == "https://github.com/pzfreo/draftwright/issues/1705"
