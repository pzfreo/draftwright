"""Unit tests for CoverageState — the lint-side coverage-signal owner
(#138 / ADR 0005, Step 3)."""

import pytest

from draftwright._core import HoleRef
from draftwright.linting import CoverageState

# Pure unit tests — no OCC builds — so they join the build-light `smoke` set (#153).
pytestmark = pytest.mark.smoke

_H1, _H2, _H3 = (HoleRef.of((1, 0, 0)), HoleRef.of((2, 0, 0)), HoleRef.of((3, 0, 0)))
_H9 = HoleRef.of((9, 0, 0))


def test_cover_pattern_records_callout_and_holes():
    c = CoverageState()
    c.cover_pattern("hc_plan0", [_H1, _H2, _H3])
    assert c.is_pattern_callout("hc_plan0")
    assert not c.is_pattern_callout("hc_plan1")
    assert c.is_hole_patterned(_H2)
    assert not c.is_hole_patterned(_H9)


def test_cover_pattern_accumulates_across_calls():
    c = CoverageState()
    c.cover_pattern("a", [_H1])
    c.cover_pattern("b", [_H2, _H3])
    assert c.is_pattern_callout("a") and c.is_pattern_callout("b")
    assert all(c.is_hole_patterned(h) for h in (_H1, _H2, _H3))


def test_dropped_diams_append_read_reset():
    c = CoverageState()
    assert c.dropped_diams == []
    c.drop_diam(8.0)
    c.drop_diam(5.0)
    assert c.dropped_diams == [8.0, 5.0]
    c.reset_dropped()
    assert c.dropped_diams == []


class TestRedundantDimensionEndToEnd:
    """#941: the case the check exists for, on a real build rather than stand-ins.

    `measured_dimension` (and imported AP242 PMI, the same IR kind) carries its own value
    and reference points and is not checked against the planner's approved set. Restating a
    measurement the planner already draws produced two dimensions saying the same thing,
    with nothing reporting it — which is what settled #941's open question: redundancy is
    reachable from the authored/materialised path, not from the planner, whose spans are
    all measured from a common datum and so cannot close a chain.
    """

    @staticmethod
    def _plate():
        from build123d import Box

        return Box(40, 20, 10)

    def _codes(self, dwg):
        return [i for i in dwg.lint() if i.code == "redundant_dimension"]

    def test_restating_an_approved_measurement_is_reported(self):
        from draftwright import Sheet

        sheet = Sheet.from_part(self._plate(), title="T", number="N").auto_dimensions()
        # The envelope width the planner already approves, stated again by hand.
        sheet.measured_dimension(
            kind="linear",
            value=40,
            label="40",
            dominant_axis="X",
            ref_bbox=(-20, -10, -5, 20, 10, 5),
            ref_pts=[(-20, 0, 0), (20, 0, 0)],
        )
        dwg = sheet.build()

        drawn = {n for n, o in dwg.iter_annotations() if getattr(o, "label", None)}
        assert {"m_env_width", "pmi_x_0"} <= drawn  # guard: both really are drawn
        issues = self._codes(dwg)
        assert len(issues) == 1, [i.message for i in issues]
        assert issues[0].severity == "warning"
        # Names both annotations, so the reader knows which two to look at.
        assert "'m_env_width'" in issues[0].message and "'pmi_x_0'" in issues[0].message

    def test_the_same_part_without_the_restatement_is_clean(self):
        """The control. Without this, the test above would pass on a check that fires on
        every drawing."""
        from draftwright import Sheet

        dwg = Sheet.from_part(self._plate(), title="T", number="N").auto_dimensions().build()
        assert not self._codes(dwg)

    def test_ordinary_datum_ladders_do_not_over_dimension(self):
        """The measured half of #941's open question: on these parts the automatic path
        emits no redundant pair, because every span it approves along a feature's own axis
        is measured from a common datum and the derived remainders are left unstated.

        Named for what it actually shows. It does NOT show that the planner cannot
        over-dimension — #958 is an automatic drawing that does, where a pad and a
        mis-recognised slot carry the identical x span and both get dimensioned. That
        fixture is deliberately absent here and the exclusion is stated rather than silent;
        add it back when #958 is fixed. ADR 0016 Amendment 2 records the corrected claim.
        """
        from build123d import Box, Cylinder, Pos

        from draftwright import build_drawing

        corpus = {
            "plate": Box(80, 50, 8),
            "plate+hole": Box(80, 50, 8) - Pos(-20, 0, 0) * Cylinder(4, 20),
            "two holes": Box(80, 50, 8)
            - Pos(-20, 0, 0) * Cylinder(4, 20)
            - Pos(20, 10, 0) * Cylinder(3, 20),
            "stepped": Box(40, 12, 40) - Pos(10, 0, 20) * Box(20, 12, 20),
            "pocket": Box(80, 60, 20) - Pos(0, 0, 14) * Box(30, 20, 14),
            "boss": Box(80, 60, 12) + Pos(0, 0, 12) * Cylinder(10, 8),
            "turned shaft": Cylinder(15, 20) + Pos(0, 0, 17.5) * Cylinder(10, 15),
            "bored flange": Cylinder(40, 8) - Cylinder(8, 20),
        }
        noisy = {
            name: [
                i.message for i in build_drawing(part).lint() if i.code == "redundant_dimension"
            ]
            for name, part in corpus.items()
        }
        assert not any(noisy.values()), {k: v for k, v in noisy.items() if v}
