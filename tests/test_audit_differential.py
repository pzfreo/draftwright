"""`draftwright.audit` — diff two builds and ask what went missing (#996 WP1 step 2).

The ledger says which rule removed a measurement, which is diagnosis. It cannot say that a
measurement left without any rule recording it — three consecutive review rounds on #999 found
suppression paths that recorded nothing, each caught by a person hunting for it rather than by
the ledger noticing its own gap.

The differential does not depend on anything having been recorded. If a dimension is present
in one build and absent in another, something removed it. That is how #997 was actually found:
not from four issue reports describing its symptoms, but from `50x50` vs `50x40`.

Most of these tests use duck-typed stand-ins rather than real builds. The subject is the
COMPARISON, and a real part would make the test about recognition instead — slower, and
failing for reasons that have nothing to do with the thing under test. One end-to-end case
keeps it honest about the real `Drawing` surface.
"""

from __future__ import annotations

from build123d import Box, Cylinder, Rot

from draftwright import build_drawing
from draftwright.audit import diff_builds, explain


class _FakeDrawing:
    """The three public reads `diff_builds` uses, and nothing else."""

    def __init__(self, dims: dict[str, str], suppressions: list[dict] | None = None):
        self._dims = dims
        self._supp = suppressions or []

    def annotations(self):
        return dict.fromkeys(self._dims, "Dimension")

    def get_annotation(self, name):
        return type("A", (), {"label": self._dims[name]})()

    def suppressions(self):
        return list(self._supp)


def _supp(parameter, reason, feature="envelope@(0,0,0)/z"):
    return {
        "feature": feature,
        "parameter_id": parameter,
        "value": None,
        "reason": reason,
        "authored": False,
    }


def test_a_loss_a_rule_accounts_for_is_not_unexplained():
    """The #997 shape, and the good case: a dimension vanished AND the ledger names why.

    That is the audit working — a human reads "square footprint (single overall dim
    suffices)" and can ask whether a 50x50 part should really have no plan size. The
    measurement is gone, but nothing is hidden.
    """
    before = _FakeDrawing({"m_env_width": "50", "m_env_depth": "40", "dim_height": "30"})
    after = _FakeDrawing(
        {"dim_height": "30"},
        [_supp("width.length", "square footprint"), _supp("depth.length", "square footprint")],
    )

    diff = diff_builds(before, after)
    assert set(diff["dimensions_lost"]) == {"m_env_width", "m_env_depth"}
    assert diff["unexplained_losses"] == {}, "the ledger accounts for both"
    assert any("square footprint" in line for line in explain(diff))


def test_a_loss_no_rule_claims_is_flagged():
    """The alarm. A dimension left the drawing and NOTHING recorded that it did.

    This is the class the ledger alone cannot detect, and the reason this module exists: the
    coincident-location dedup dropped candidates before the compiler saw them, so no
    `Omission` was ever written. A diff notices anyway, because it compares outcomes rather
    than trusting the engine's account of itself.
    """
    before = _FakeDrawing({"m_locx0": "33", "dim_height": "30"})
    after = _FakeDrawing({"dim_height": "30"})  # m_locx0 gone, ledger silent

    diff = diff_builds(before, after)
    assert diff["unexplained_losses"] == {"m_locx0": "33"}
    assert explain(diff)[0].startswith("UNEXPLAINED"), "the alarm must lead the report"


def test_the_report_puts_the_alarm_first():
    """Ordering is the value, not decoration. An unexplained loss is a possible engine defect;
    an explained one is a rule working. Interleaved in dict order, the first hides among the
    second — which is how a wrong suppression survived four issue reports."""
    before = _FakeDrawing({"m_locx0": "33", "m_env_depth": "40"})
    after = _FakeDrawing({"m_new": "12"}, [_supp("depth.length", "square footprint")])

    lines = explain(diff_builds(before, after))
    assert lines[0].startswith("UNEXPLAINED")
    assert sum(line.startswith("UNEXPLAINED") for line in lines) == 1


def test_no_difference_reports_nothing():
    """No false positives: two identical builds produce an empty report, or the signal is
    noise and nobody reads it."""
    dwg = _FakeDrawing({"m_env_width": "50"}, [_supp("depth.length", "rotational OD")])
    diff = diff_builds(dwg, dwg)
    assert diff["dimensions_lost"] == diff["dimensions_gained"] == {}
    assert diff["suppressions_gained"] == diff["suppressions_lost"] == []
    assert explain(diff) == []


def test_it_works_on_real_drawings():
    """One end-to-end case, so the duck types above cannot drift from the real surface.

    An X-turned shaft suppresses its cross-axis extent via a LIVE rule (the OD conveys it), so
    this exercises a genuine ledger entry rather than a fabricated one.
    """
    prismatic = build_drawing(Box(60, 40, 20), number="X")
    turned = build_drawing(Rot(0, 90, 0) * Cylinder(10, 40), number="X")

    diff = diff_builds(prismatic, turned)
    assert "m_env_depth" in diff["dimensions_lost"], "the turned part states no depth"
    reasons = " ".join(r for _, _, r in diff["suppressions_gained"])
    assert "rotational OD" in reasons, "and the ledger says which rule took it"
