"""#1229 — three seams in the hole-requirement ledger's member keying.

All three were latent: none produced a wrong answer on any fixture. They are worth closing
anyway because each is a place where the ledger's own join contract is stated in one place and
quietly not honoured in another, which is how #1223's real defect (a consumer keying a different
axis space from the ledger) got in.

1. `hole_requirement_outcomes` builds `HoleRequirementOutcome` at three sites. Two passed
   `members`; the `unmatched_countersinks` tail did not, so those outcomes carried the `()`
   default and could not be joined by site at all. Fixed in `hole_coverage.py`, tested in
   `test_issue_1143_hole_completeness.py`. The first fix passed the seat's own opening centre —
   a RAW world coordinate in a field published in `canonical_hole_sites` space, which aliased
   the bore's own key on the fixture and moved when the part moved. It now carries the canonical
   site of the hole the seat matched.

2. `_members`' grouped branch decided `through` with `all(...)` over the members while the
   single-record branch decided it per hole. Equivalent in fact, misleading as code — see
   `TestAGroupCannotMixThroughAndBlind`, which measures the invariant that makes it so.

3. The module-level imports in `evaluation/step_analysis.py`. Filed as "unexplained"; measuring
   showed the filing was wrong — see that module's docstring, and
   `TestTheEvaluationModuleStaysCheapToImport` here.

Seam 1 is tested where it belongs — `tests/test_issue_1143_hole_completeness.py::
test_unmatched_second_face_countersink_fails_closed_in_hole_ledger`, which has exercised that
branch since #1151 and now also asserts the member sites and their space.

I first claimed the branch was unreachable and shipped an AST guard over the construction sites
instead. That was wrong three ways, and the correction is worth keeping written down: the branch
is reachable; a fixture and a passing test for it were already in the repo, findable by one grep;
and the guard was not load-bearing anyway — it checked for the `members` KEYWORD, so it survived
`members=()`, survived an aliased constructor two lines below, and could be pushed out of range
by a comment. The behavioural test kills all of those. A structural guard is not a substitute for
a reachable test; it is what you write when there is genuinely no way in, and I did not check.
"""

from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

from b123d_recognisers import HoleSpec


class TestAGroupCannotMixThroughAndBlind:
    """Why `_members` may read `bottom` off one member rather than folding over all of them.

    `bottom` is a `HoleSpec` field and hole patterns are built by grouping on `_spec_key`, so
    the members of a group share it by construction. This measures that rather than asserting
    it — the equivalence is the whole reason the `all(...)` was safe, and if the recogniser ever
    groups more loosely, this fails before the ledger starts keying blind holes as through.

    Be exact about what the change to `_members` did and did not do. Reverting it to read
    `recognised[0].bottom` off the raw record SURVIVES the suite, and correctly:
    `HoleSpec.from_hole` copies `bottom` across verbatim, so the spec adds no normalisation
    here. It is expressive, not behavioural — it names the field that defines the group. That
    is NOT true of `axis` on the line above, where the spec's 6-dp rounding is load-bearing and
    reverting it changes the reported coordinates (#1223 review). Two adjacent lines, one
    equivalent mutant and one real one; worth not confusing them.
    """

    def test_bottom_is_part_of_the_spec(self):
        assert "bottom" in {f.name for f in __import__("dataclasses").fields(HoleSpec)}, (
            "HoleSpec no longer carries `bottom`, so a spec group may mix through and blind "
            "and `_members` must fold over the members again (#1229)"
        )

    @staticmethod
    def _mixed_part():
        """Three THROUGH and three BLIND holes of the same diameter on the same axis.

        The first version of this test used six identical through holes, so `len(bottoms) == 1`
        held by construction and the assertion could not fail however the recogniser grouped
        (#1229 review). This part can exhibit the defect: if grouping ever stopped keying on
        `bottom`, these six would land in one group with two bottoms.
        """
        from build123d import Align, Box, Cylinder, Pos

        centre = (Align.CENTER, Align.CENTER, Align.CENTER)
        part = Box(160, 60, 20, align=centre)
        for x in (-60, -40, -20):
            part -= Pos(x, 0, 0) * Cylinder(4, 60, align=centre)
        for x in (20, 40, 60):
            part -= Pos(x, 0, 5) * Cylinder(4, 12, align=centre)
        return part

    def test_the_part_really_mixes_through_and_blind(self):
        # The precondition. Without both kinds present the guard below is the tautology the
        # first version of it was.
        from draftwright.builder import build_drawing

        recognition = build_drawing(self._mixed_part(), title="T", number="N-1").recognition()
        assert {HoleSpec.from_hole(h).bottom for h in recognition.holes} >= {"through", "flat"}, (
            sorted({HoleSpec.from_hole(h).bottom for h in recognition.holes})
        )

    def test_every_recognised_group_is_uniform_in_bottom(self):
        from draftwright.builder import build_drawing

        recognition = build_drawing(self._mixed_part(), title="T", number="N-1").recognition()
        groups = list(getattr(recognition, "hole_patterns", ()))
        assert len(groups) >= 2, (
            "the mixed part produced fewer than two groups; either grouping no longer splits on "
            "`bottom` — the thing this guards — or the fixture stopped recognising"
        )
        checked = 0
        for group in groups:
            bottoms = {HoleSpec.from_hole(h).bottom for h in group.holes}
            checked += len(group.holes)
            assert len(bottoms) == 1, (group, bottoms)
        assert checked >= 6, f"only {checked} grouped holes examined"


class TestTheEvaluationModuleStaysCheapToImport:
    """#1229's third item, inverted: the in-function imports are load-bearing, not strays."""

    def test_importing_the_evaluation_module_does_not_pull_the_engine(self):
        # A subprocess, because the engine is already imported in this one. Asserting on
        # `sys.modules` in-process would pass no matter what the module does.
        code = (
            "import sys, draftwright.evaluation.step_analysis;"
            "print(int(any(m.startswith('build123d') for m in sys.modules)))"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True
        )
        assert result.stdout.strip() == "0", (
            "importing draftwright.evaluation.step_analysis now pulls build123d, which takes "
            "~1.8 s. Its draftwright imports are deliberately inside function bodies (#313); "
            "see that module's docstring for the measurements (#1229)."
        )

    def test_the_module_has_no_module_level_draftwright_import(self):
        spec = importlib.util.find_spec("draftwright.evaluation.step_analysis")
        assert spec is not None and spec.origin is not None
        tree = ast.parse(Path(spec.origin).read_text())
        # BOTH `from x import y` and plain `import x`. Matching only `ImportFrom` left the
        # test named for this property unable to see the commonest form; a mutation adding
        # `import draftwright.linting.hole_coverage` was caught only by the subprocess test
        # beside it (#1229 review).
        offenders = [
            node.lineno
            for node in tree.body
            if (
                isinstance(node, ast.ImportFrom)
                and (node.module or "").startswith(("draftwright", "build123d"))
            )
            or (
                isinstance(node, ast.Import)
                and any(a.name.startswith(("draftwright", "build123d")) for a in node.names)
            )
        ]
        assert not offenders, (
            f"module-level engine import at line(s) {offenders}; keep them in function bodies"
        )
