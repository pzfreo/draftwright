"""ADR 0018's motivating failure, reproduced from a synthetic part.

The ADR was proposed from a user-supplied `worm_planetary_concept_Alimacznicy.step` that this
repository does not have, and its first required-evidence item is:

    A synthetic thin rotational plate reproduces the A1/fixed-four-view failure without relying
    on a proprietary or externally supplied STEP file.

This is that fixture. It exists so every later slice of #1130 is measured against something the
repository owns, and so the claim "the fixed four-view topology forces the sheet" is a number
here rather than a recollection of someone else's file.

**These tests pin the safety counterexample, deliberately.** Nothing here is a defect report
against the packer — given four views the engine's choice is correct, and its refusal to fit A2
is honest. Automatic selection now tries the smaller profile + end-view set, but this fixture
loses required slot annotations under that layout, so the finished-drawing gate retains the
full topology.

A slice that changes these numbers is doing ADR 0018's work, and must update them and say so.
"""

from __future__ import annotations

import math

import pytest
from build123d import Box, Cylinder, Pos, Rot

from draftwright.builder import ScaleIncompatibilityError, build_drawing

#: The case study's measurements, from ADR 0018 "Case study: thin X-axis worm planetary plate":
#: "Geometry bbox: 43 x 217 x 217 mm. It is a thin, predominantly rotational X-axis component
#: with two concentric hole patterns, several coaxial diameters, an axial stack, pockets and a
#: central keyway."
_BBOX = (43.0, 217.0, 217.0)


def thin_rotational_plate():
    """A thin X-axis rotational plate carrying the case study's feature vocabulary.

    Not a copy of the user's part — a synthetic stand-in with the same bounding box, axis and
    kinds of features, which is what the evidence item asks for. The disc radius and hub stack
    are chosen to hit 43 x 217 x 217 exactly.
    """
    part = Rot(0, 90, 0) * Cylinder(108.5, 12)
    # The axial stack, each tier STARTING where the last ends. The first version left a 2.5 mm
    # gap between them, so the second tier floated: build123d 0.11 tolerates that as a
    # two-solid `Compound` and 0.10 returns a `ShapeList`, which `build_drawing` then tried to
    # open as a file path. A disconnected part was never the intent — every measurement taken
    # from this fixture was taken from a part in two pieces (#1130).
    part += Rot(0, 90, 0) * Pos(0, 0, 12) * Cylinder(45, 18)  # z 3 .. 21
    part += Rot(0, 90, 0) * Pos(0, 0, 28.75) * Cylinder(28, 15.5)  # z 21 .. 36.5
    part -= Rot(0, 90, 0) * Cylinder(16, 60)  # central bore
    part -= Pos(30, 0, 0) * Box(60, 8, 20)  # keyway
    for index in range(6):  # outer concentric hole pattern
        angle = index * math.pi / 3
        part -= (
            Rot(0, 90, 0) * Pos(85 * math.cos(angle), 85 * math.sin(angle), 0) * Cylinder(6, 40)
        )
    for index in range(4):  # inner concentric hole pattern
        angle = index * math.pi / 2 + math.pi / 4
        part -= (
            Rot(0, 90, 0) * Pos(60 * math.cos(angle), 60 * math.sin(angle), 0) * Cylinder(4.5, 40)
        )
    return part


def test_the_synthetic_plate_matches_the_case_studys_geometry():
    """The precondition for every other assertion here: this really is the ADR's part.

    Without it the fixture could drift into some other shape and the numbers below would still
    look like evidence for ADR 0018 while describing something else.
    """
    part = thin_rotational_plate()
    box = part.bounding_box()
    measured = (box.size.X, box.size.Y, box.size.Z)
    assert measured == pytest.approx(_BBOX, abs=0.5), (
        f"the fixture is {measured}, not the case study's {_BBOX}"
    )
    # ONE solid. The first version left a 2.5 mm gap in the axial stack, so the top tier floated
    # — and a bounding box cannot see that, which is why every measurement taken from this
    # fixture was taken from a part in two pieces. build123d 0.11 tolerates it as a `Compound`
    # while 0.10 returns a `ShapeList` that `build_drawing` tries to open as a file path, so it
    # passed here and failed every Linux CI shard (#1130).
    assert len(part.solids()) == 1, (
        f"the fixture is in {len(part.solids())} pieces; a disconnected stand-in is not the "
        "case study's part and behaves differently across build123d versions"
    )


@pytest.fixture(scope="module")
def automatic():
    """One automatic build, shared. Measured at ~2 s; three separate builds cost 5 s and the
    repo's guidance is that a critique-style test shares a module-scoped drawing rather than
    minting a dense fixture per assertion. NOT marked slow: a slice that moves these numbers
    should fail in the fast tier, where the author sees it, not post-merge (#153)."""
    return build_drawing(thin_rotational_plate(), title="T", number="N")


class TestTheFixedTopologyForcesTheSheet:
    def test_the_automatic_result_is_a1_at_full_scale_and_reports_no_problem(self, automatic):
        """The ADR's headline: A1 landscape at 1:1 for a part 43 mm thick.

        The sheet is the ADR's subject and is unchanged because the automatic reduced candidate
        loses required outcomes. #1250's completeness gate keeps that loss explicit.
        """
        drawing = automatic

        assert (drawing.page_w, drawing.page_h) == (841.0, 594.0), "not A1 landscape"
        assert drawing.scale == 1.0
        assert set(drawing.views) == {"front", "plan", "side", "iso"}
        assert drawing.view_decision["status"] == "retained_after_rejection"
        # The sheet is still A1 at 1:1 — the fixed four-view topology still forces it, which is
        # what ADR 0018 exists to fix and has not fixed yet. What HAS changed is the second
        # half of the original claim: the drawing no longer says it is fine. Since #1250 the
        # automatic path runs the same requirement gate as the explicit one and records
        # `plan_incomplete` when the settled drawing loses a required outcome.
        assert {i.code for i in drawing.lint() if i.severity == "error"} == {"plan_incomplete"}
        assert drawing.lint_summary()["passed"] is False

    def test_the_plan_view_repeats_the_front_and_carries_almost_nothing(self, automatic):
        """WHY it is the wrong sheet, not just that it is a big one.

        On an X-axis rotational part the front and plan are both edge-on: same silhouette, same
        extent. One of them is a second look at the same thing, and the annotations show which —
        the disc face (side) carries the hole patterns and diameters, the front carries the
        axial dimensions, and the plan carries almost nothing while occupying 217 mm.
        """
        drawing = automatic
        front = drawing.view_bounds("front")
        plan = drawing.view_bounds("plan")
        side = drawing.view_bounds("side")

        front_size = (front[2] - front[0], front[3] - front[1])
        plan_size = (plan[2] - plan[0], plan[3] - plan[1])
        assert plan_size == pytest.approx(front_size, abs=0.5), (
            f"front {front_size} and plan {plan_size} are no longer the same projection, so "
            "this part is no longer the redundant-view case the ADR describes"
        )
        assert (side[2] - side[0]) == pytest.approx(side[3] - side[1], abs=0.5), (
            "the side view is no longer the square disc face"
        )

        counts: dict = {}
        for name in drawing.registry.names():
            counts[drawing.view_of(name)] = counts.get(drawing.view_of(name), 0) + 1
        assert counts.get("plan", 0) <= 2, (
            f"the plan view now carries {counts.get('plan', 0)} annotations, so it is no longer "
            f"the near-empty duplicate this case is about: {counts}"
        )
        assert counts.get("side", 0) > 5 * counts.get("plan", 0), (
            f"the disc face no longer dominates the annotation load: {counts}"
        )

    def test_the_automatic_sheet_is_one_the_engine_would_refuse_if_asked_for_it(self, automatic):
        """The sharp end of the evidence, and the defect #1250 fixed.

        Before #1250 the automatic build chose A1 at 1:1 and reported `passed: True` with no
        lint errors. Asking for that SAME page and scale explicitly made the engine refuse —
        "requested scale 1 cannot preserve required annotations". Same part, same sheet, same
        scale, two verdicts, decided by how the caller phrased the request: the explicit path
        ran `_scale_blockers` and the automatic path did not.

        The blockers are real, not an artefact of the stricter path: the automatic drawing
        still carries `slot_dim_dropped` and `hole_requirement_missing`, so it IS the
        incomplete drawing the explicit gate exists to prevent.

        ADR 0018's evidence list requires: "A forced small sheet/large scale that drops a
        requirement is rejected, not accepted with a warning-only incomplete drawing." The
        automatic path now runs the same gate and reports the settled drawing's loss at error
        severity. Candidate search remains the joint planner's responsibility (#1262), because
        partial registry provenance cannot prove that a rebuilt candidate preserves everything.

        The first version of this test asserted that A2 at 1:1 raises, and read that as the
        four-view topology forcing the sheet. It does raise — but so does A1, so the assertion
        demonstrated this inconsistency rather than the sheet cost it claimed. The mutation that
        found it changed `page="A2"` to `page="A1"` and the test still passed.
        """
        assert automatic.lint_summary()["passed"] is False, (
            "the automatic path is reporting success again — #1250 has regressed"
        )
        dropped = {issue.code for issue in automatic.lint()}
        assert {"slot_dim_dropped", "hole_requirement_missing"} <= dropped, (
            f"the automatic drawing no longer loses requirements, so there is nothing "
            f"inconsistent about accepting it: {sorted(dropped)}"
        )

        with pytest.raises(ScaleIncompatibilityError) as raised:
            build_drawing(
                thin_rotational_plate(),
                page="A1",
                scale=1.0,
                title="T",
                number="N",
            )
        message = str(raised.value)
        assert "cannot preserve required annotations" in message
        assert "slot_dim_dropped" in message, (
            f"the refusal no longer names the requirements it would lose: {message}"
        )

        # The two verdicts now AGREE, which is the fix. The explicit path refuses; the
        # automatic path cannot refuse — the caller made no claim and has no lever, so
        # raising would break a build with no remedy — but it records the same loss at error
        # severity and names the same measurements.
        decision = automatic.scale_decision
        assert decision["status"] == "incomplete"
        assert {item["code"] for item in decision["blockers"]} <= dropped
        summary = next(i for i in automatic.lint() if i.code == "plan_incomplete")
        assert summary.measurement_ids, "the summary must stay addressable (ADR 0010/0016)"
