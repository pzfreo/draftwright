"""Manufacturing-completeness check (#996 WP1 step 3).

Two of these tests exist because the module was wrong in that exact way first, and both
wrongnesses had the same shape: the checker inventing a defect rather than finding one. That
is the failure mode this whole epic is about, so they are canaries, not decoration.
"""

from build123d import Axis, Box, Cylinder, Pos, chamfer, fillet

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
        "by_kind": summary["by_kind"],  # asserted properly in test_by_kind_counts_features
    }
    assert summary["complete"] + summary["undefined"] == summary["judged"]


def _ledger_ids(part):
    dwg = build_drawing(part)
    return {k["parameter_id"] for n, _a in dwg.iter_annotations() for k in dwg.measurement_keys(n)}


def test_every_required_id_is_one_the_compiler_actually_emits():
    """Fail-closed ratchet on the id vocabulary — the guard that was missing.

    `_BASE_REQUIRED` named `location_slot.location`; the compiler emits
    `location_slot.length`. So EVERY slot in every drawing reported its location missing, and
    nothing caught it — the docstring above the table even warned that a typo there "would
    silently mean never covered and read as a real finding", which is precisely what it did.

    A hand-maintained string table with no ratchet is an unenforced claim. This builds real
    parts and asserts every id the table demands is one the compiler genuinely mints.
    """
    from draftwright.linting.completeness import _BASE_REQUIRED

    emitted = set()
    for part in (
        Box(60, 30, 10) - Box(30, 8, 20),  # slot
        Box(80, 60, 20) - Pos(0, 0, 12) * Box(30, 20, 10),  # pocket
        chamfer(Box(60, 40, 20).edges().filter_by(Axis.Z), 3),
        fillet(Box(60, 40, 20).edges().filter_by(Axis.Z), 5),
    ):
        emitted |= _ledger_ids(part)

    demanded = {i for size, loc in _BASE_REQUIRED.values() for i in (size | loc)}
    assert demanded <= emitted, (
        f"these ids are demanded but no build emits them: {sorted(demanded - emitted)}. "
        "A required id the compiler never mints means that feature can NEVER read covered."
    )


def test_a_real_slot_reads_covered_on_both_axes():
    """The regression finding 1 produced: no slot could ever read location-covered, and
    there was no slot integration test despite slots being one of four judged kinds."""
    part = Box(60, 30, 10) - Box(30, 8, 20)
    dwg = build_drawing(part)
    (slot,) = _verdicts(part, dwg, "slot")
    assert (slot.size, slot.location) == (COVERED, COVERED)


def test_an_edge_anchored_pocket_owes_no_location_dimension():
    """Requirements are a function of the RECORD, not the kind.

    A pocket flush with the stock edges is located by them, so the planner emits no location
    dimension — correctly. The kind-only table demanded one and reported this correct drawing
    as defective. `edge_anchored` is a recogniser fact, so reading it stays inside the ADR
    0015 carve-out.
    """
    from draftwright.recognition import recognise_pockets

    part = Box(80, 60, 20) - Pos(25, 20, 12) * Box(30, 20, 10)
    assert any(getattr(p, "edge_anchored", False) for p in recognise_pockets(part)), (
        "fixture must actually be edge-anchored or this proves nothing"
    )
    dwg = build_drawing(part)
    (pocket,) = _verdicts(part, dwg, "pocket")
    assert pocket.location == COVERED, "nothing is owed, so nothing is missing"
    assert pocket.complete


def test_two_indistinguishable_features_are_unchecked_not_merged():
    """A complete neighbour must not lend its ids to an incomplete one.

    The join matches on kind plus a point within *tol*, so two same-kind features inside that
    cube cannot be separated. Merging their ids would let one cover the other; the honest
    answer is that neither can be judged.
    """
    from draftwright.linting import completeness

    class _Feat:
        def __init__(self, at):
            self.at = at

    part = Box(60, 40, 20)
    dwg = build_drawing(part)
    original = completeness._inventory
    try:
        completeness._inventory = lambda p, a: [
            ("chamfer", [_Feat((0.0, 0.0, 0.0)), _Feat((0.1, 0.0, 0.0))])
        ]
        verdicts = feature_completeness(part, dwg, tol=0.6)
    finally:
        completeness._inventory = original
    assert len(verdicts) == 2
    assert all(v.size == UNCHECKED and v.location == UNCHECKED for v in verdicts)


def test_by_kind_counts_features_not_axes():
    """One fully covered pocket is ONE complete feature, not two covered axes."""
    verdicts = [
        FeatureVerdict("pocket", "pocket@(0,0,0)", COVERED, COVERED),
        FeatureVerdict("pocket", "pocket@(9,0,0)", UNCOVERED, COVERED),
        FeatureVerdict("hole", "hole@(2,0,0)", UNCHECKED, UNCHECKED),
    ]
    assert completeness_summary(verdicts)["by_kind"] == {
        "pocket": {"complete": 1, "undefined": 1, "unchecked": 0},
        "hole": {"complete": 0, "undefined": 0, "unchecked": 1},
    }
