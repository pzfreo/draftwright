"""Manufacturing-completeness check (#996 WP1 step 3).

Two of these tests exist because the module was wrong in that exact way first, and both
wrongnesses had the same shape: the checker inventing a defect rather than finding one. That
is the failure mode this whole epic is about, so they are canaries, not decoration.
"""

from build123d import Axis, Box, Cylinder, Pos, chamfer

from draftwright import build_drawing
from draftwright.linting.completeness import (
    COVERED,
    UNCHECKED,
    UNCOVERED,
    FeatureVerdict,
    completeness_summary,
    feature_completeness,
)


def _pocketed():
    return Box(80, 60, 20) - Pos(0, 0, 12) * Box(30, 20, 10)


def _verdicts(part, dwg, kind):
    return [v for v in feature_completeness(part, dwg) if v.kind == kind]


def test_a_fully_dimensioned_pocket_reads_covered():
    """The regression that killed the first implementation.

    It matched dimension WITNESS GEOMETRY, and reported this pocket undefined on both axes —
    while the drawing carried its width, length and depth on one compound `m_pocket_*` leader
    (which has no witnesses at all) and its position on `m_locx`/`m_locy`. A checker that
    reports a correct drawing as defective is worse than no checker.
    """
    part = _pocketed()
    dwg = build_drawing(part)
    (pocket,) = _verdicts(part, dwg, "pocket")
    assert (pocket.size, pocket.location) == (COVERED, COVERED)
    assert "pocket_depth.length" in pocket.drawn  # the leader's compound id, not a witness


def test_removing_the_size_callout_is_reported():
    """The other direction — a check that never fires is worthless.

    Drops the compound callout and nothing else, so `location` must stay covered: the check
    has to discriminate the two, not collapse to one per-feature boolean.
    """
    part = _pocketed()
    dwg = build_drawing(part)
    for name, _ann in list(dwg.iter_annotations()):
        if name.startswith("m_pocket"):
            dwg.remove(name)
    (pocket,) = _verdicts(part, dwg, "pocket")
    assert pocket.size == UNCOVERED
    assert pocket.location == COVERED, "only the size was removed"
    assert pocket.undefined and not pocket.complete


def test_four_chamfers_are_four_features_and_all_are_covered():
    """`_anchor` fell back to ``(0, 0, 0)`` for records with no ``.location``/``.frame``, so
    all four chamfers collapsed onto ONE key, matched none of the four identities actually
    drawn for them, and were reported undefined — four fabricated alarms on a part that is
    correctly dimensioned. Chamfer and Fillet anchor on ``.at``.

    Asserts four DISTINCT keys, not just four verdicts: the collapse is the defect, and four
    identical keys would satisfy a count.
    """
    part = chamfer(Box(60, 40, 20).edges().filter_by(Axis.Z), 3)
    dwg = build_drawing(part)
    chamfers = _verdicts(part, dwg, "chamfer")
    assert len(chamfers) == 4
    assert len({v.key for v in chamfers}) == 4, "one key per chamfer, not one for all four"
    assert all(v.size == COVERED for v in chamfers)
    assert all(v.location == COVERED for v in chamfers), "a chamfer needs no location dim"


def test_a_feature_with_no_derivable_anchor_is_unchecked_not_undefined():
    """No anchor means the join cannot run, so nothing can be concluded — and the honest
    answer is *unknown*. Reporting it as undefined is how the four fabricated chamfer alarms
    happened; this pins the polarity at the join itself."""

    class _Anchorless:  # no location / at / frame / width_axis
        pass

    from draftwright.linting import completeness

    part = _pocketed()
    dwg = build_drawing(part)
    original = completeness._inventory
    try:
        completeness._inventory = lambda p, a: [("pocket", [_Anchorless()])]
        (verdict,) = feature_completeness(part, dwg)
    finally:
        completeness._inventory = original
    assert (verdict.size, verdict.location) == (UNCHECKED, UNCHECKED)
    assert not verdict.undefined, "unknown is not a defect"
    assert not verdict.complete, "and it is not a pass either"


def test_a_hole_is_listed_but_unchecked_while_its_renderer_records_no_identity():
    """Hole callouts are on the legacy pre-compiled-plan surface (#926) and record nothing,
    so a hole's size cannot be judged. It must still APPEAR — a feature missing from the
    report reads as "nothing to say about it", which is the silence this epic exists to
    remove. Delete this test when #926 lands; the hole should then be judged."""
    part = Box(80, 60, 10) - Pos(20, 10, 0) * Cylinder(4, 20)
    dwg = build_drawing(part)
    (hole,) = _verdicts(part, dwg, "hole")
    assert (hole.size, hole.location) == (UNCHECKED, UNCHECKED)


def test_unchecked_is_never_folded_into_either_side():
    """Reporting "83% complete" while a third of the features were never examined is exactly
    the false confidence #996 exists to remove, so the summary keeps three numbers."""
    verdicts = [
        FeatureVerdict("pocket", "pocket@(0,0,0)", COVERED, COVERED),
        FeatureVerdict("slot", "slot@(1,0,0)", UNCOVERED, COVERED),
        FeatureVerdict("hole", "hole@(2,0,0)", UNCHECKED, UNCHECKED),
    ]
    summary = completeness_summary(verdicts)
    assert summary == {
        "features": 3,
        "judged": 2,
        "complete": 1,
        "undefined": 1,
        "unchecked": 1,
        "by_kind": summary["by_kind"],
    }
    assert summary["complete"] + summary["undefined"] == summary["judged"]
