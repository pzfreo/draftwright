"""Drawing repair behavior."""

from _parts import holed_plate as _holed_plate
from _parts import uniform_staircase as _uniform_staircase
from build123d import Box

from draftwright import build_drawing
from draftwright.linting import LintIssue


class TestRepair:
    """#30/#521: repair is a narrow safety net, not a second placement engine."""

    def test_repair_does_not_fixed_step_annotation_overlap(self):
        # Two dimensions forced onto the same page location → their labels collide. The
        # solver path owns placement; repair must not hide this with a fixed-step nudge.
        from draftwright._core import _dim

        dwg = build_drawing(Box(60, 40, 20))
        d = dwg.draft
        p1, p2 = (40.0, 20.0, 0.0), (80.0, 20.0, 0.0)
        dwg._add(_dim(p1, p2, "above", 8, d, label="AA"), "ov1")
        dwg._add(_dim(p1, p2, "above", 8, d, label="BB"), "ov2")
        assert [i for i in dwg.lint() if i.code == "annotation_overlap"]

        dwg.repair()
        assert dwg.get_annotation("ov1")._dw_spec.distance == 8
        assert dwg.get_annotation("ov2")._dw_spec.distance == 8
        assert [i for i in dwg.lint() if i.code == "annotation_overlap"]

    def test_repair_dim_inside_part_flips_side(self):
        # dim_inside_part is dormant in the multi-view sheet (lint passes no
        # part_bbox), so drive the repair directly: a wrong-side dim flips to
        # the opposite side and keeps its name binding.
        from draftwright._core import _dim
        from draftwright.repair import _repair_dim_inside_part

        dwg = build_drawing(Box(60, 40, 20))
        dim = dwg._add(_dim((0, 0, 0), (40, 0, 0), "above", 8, dwg.draft, label="INSIDE"), "x")
        assert dim._dw_spec.side == "above"

        issue = LintIssue(
            severity="warning",
            message="Dim 'INSIDE': annotation bbox overlaps part outline by 40%",
            code="dim_inside_part",
        )
        assert _repair_dim_inside_part(dwg, issue) is True
        new = dwg.get_annotation("x")
        assert new is not dim
        assert new._dw_spec.side == "below"
        assert new in dwg.items and dim not in dwg.items

    def test_repair_inside_part_attempted_once_no_oscillation(self):
        # A side flip that does not help must not be re-flipped (oscillation).
        # The same label is only flipped once across the whole loop.
        from draftwright._core import _dim

        dwg = build_drawing(Box(60, 40, 20))
        dwg._add(_dim((0, 0, 0), (40, 0, 0), "above", 8, dwg.draft, label="OSC"), "x")

        # Monkeypatch lint to always report the same dim_inside_part.
        issue = LintIssue(
            severity="warning",
            message="Dim 'OSC': annotation bbox overlaps part outline by 40%",
            code="dim_inside_part",
        )
        # `physical=` because repair asks for the placement critique only (#1022).
        dwg.lint = lambda **kw: [issue]
        dwg.repair(max_iter=5)
        # Flipped exactly once → ends on "below", not back to "above".
        assert dwg.get_annotation("x")._dw_spec.side == "below"

    def test_repair_idempotent_on_clean_drawing(self):
        # build_drawing already repairs by default, so a second pass is a no-op:
        # same objects, same order.
        dwg = build_drawing(Box(60, 40, 20))
        before = [id(o) for o in dwg.items]
        assert dwg.repair() is dwg
        assert [id(o) for o in dwg.items] == before

    def test_repair_does_not_increase_issue_counts(self):
        # Acceptance: on the existing fixtures, error+warning counts after the
        # repair pass are <= the raw greedy placement — no regressions.
        def ew(dwg):
            return sum(1 for i in dwg.lint() if i.severity in ("error", "warning"))

        for part in (Box(60, 40, 20), _holed_plate(), _uniform_staircase()):
            raw = ew(build_drawing(part, repair=False))
            fixed = ew(build_drawing(part, repair=True))
            assert fixed <= raw

    def test_repair_ignores_annotation_overlap_without_mutation(self):
        # annotation_overlap is no longer repairable (#521). It remains visible
        # to lint rather than being moved by a fixed-step fallback.
        from draftwright._core import _dim

        dwg = build_drawing(Box(60, 40, 20))
        orig = dwg._add(_dim((0, 0, 0), (40, 0, 0), "above", 8, dwg.draft, label="RB"), "x")
        overlap = LintIssue(
            severity="warning",
            message="labels 'RB' and 'QQ' overlap",
            code="annotation_overlap",
        )
        calls = {"n": 0}

        def fake_lint(**kw):  # repair lints physical=False (#1022)
            calls["n"] += 1
            return [overlap]

        dwg.lint = fake_lint
        dwg.repair(max_iter=3)
        assert dwg.get_annotation("x") is orig
        assert dwg.get_annotation("x")._dw_spec.distance == 8
        assert calls["n"] == 1

    def test_build_drawing_repair_flag_is_respected(self):
        # repair=False leaves the greedy placement untouched; the default repairs.
        from draftwright._core import _dim

        # A clean part is identical either way (nothing to repair).
        a = build_drawing(Box(60, 40, 20), repair=False)
        b = build_drawing(Box(60, 40, 20), repair=True)
        assert [getattr(o, "label", None) for o in a.items] == [
            getattr(o, "label", None) for o in b.items
        ]
        # The factory tags engine dims so repair can re-place them.
        d = _dim((0, 0, 0), (40, 0, 0), "above", 8, a.draft, label="Z")
        assert d._dw_spec.side == "above"
