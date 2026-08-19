"""#1229 — three seams in the hole-requirement ledger's member keying.

All three were latent: none produced a wrong answer on any fixture. They are worth closing
anyway because each is a place where the ledger's own join contract is stated in one place and
quietly not honoured in another, which is how #1223's real defect (a consumer keying a different
axis space from the ledger) got in.

1. `hole_requirement_outcomes` builds `HoleRequirementOutcome` at three sites. Two passed
   `members`; the `unmatched_countersinks` tail did not. The answer is that it SHOULD be empty,
   explicitly — see `hole_coverage.py` and
   `test_issue_1143_hole_completeness.py::test_an_unmatched_countersink_is_still_counted_and_still_unattributable`.

   Two attempts to give it a key failed first. The seat's own opening centre is a raw world
   coordinate in a canonical field: measured, it was `(0, 0, 6)` on the fixture — aliasing
   nothing as authored — and became `(0, 0, 0)` and collided with the bore's key once the solid
   was translated by −6. (An earlier draft of this paragraph said it collided as authored; the
   harness that "measured" that had pooled the ATTACHED seat's members, which legitimately carry
   the bore's site, with the tail's.) Looking up the hole the seat matched is in the right space,
   but `countersink_matches_hole` can accept more than one hole, so it needs a rule for choosing
   — and which hole a seat sits on is the recogniser's question, not a lint module's.

2. `_members`' grouped branch decided `through` with `all(...)` over the members while the
   single-record branch decided it per hole. Equivalent in fact, misleading as code — see
   `TestAGroupCannotMixThroughAndBlind`, which measures the invariant that makes it so.

3. The IN-FUNCTION imports in `evaluation/step_analysis.py` — the module has no module-level
   engine imports at all, which is the seam. Filed as "unexplained"; measuring showed the filing
   was wrong — see that module's docstring, and `TestTheEvaluationModuleStaysCheapToImport`.

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

_ENGINE = ("draftwright", "build123d", "b123d_recognisers")


class TestAGroupCannotMixThroughAndBlind:
    """Why `_members` may read `bottom` off one member rather than folding over all of them.

    `bottom` is a `HoleSpec` field and hole patterns are built by grouping on `_spec_key`, so the
    members of a group share it by construction. This measures that rather than asserting it.

    The fixture is INTERLEAVED — through at x=−60/−20/20, blind at −40/0/40, one pitch — because
    the first two attempts were tautologies. Six identical through holes cannot mix by
    construction; and three-through-then-three-blind split positionally at the gap between them,
    so the assertion held however the spec key was mutated. Interleaved, only the spec key can
    separate them: neutering it collapses all six into one `LinearArray` carrying
    `{'through', 'flat'}`, which fails this test (#1229 review rounds 2 and 3).

    Note `bottom` is not the only field doing the work — `HoleSpec.from_hole` sets
    `depth = None if bottom == "through" else hole.depth`, so `depth is None` iff the hole is
    through, and either field separates the two kinds. The invariant `_members` relies on is the
    GROUPING, which is what this asserts.

    Be exact about what the change to `_members` did and did not do. Reverting it to read
    `recognised[0].bottom` off the raw record SURVIVES the suite, and correctly:
    `HoleSpec.from_hole` copies `bottom` across verbatim, so the spec adds no normalisation
    here. It is expressive, not behavioural — it names the field that defines the group. That
    is NOT true of `axis` on the line above, where the spec's 6-dp rounding is load-bearing and
    reverting it changes the reported coordinates (#1223 review). Two adjacent lines, one
    equivalent mutant and one real one; worth not confusing them.
    """

    def test_the_spec_separates_through_from_blind(self):
        fields = {f.name for f in __import__("dataclasses").fields(HoleSpec)}
        assert {"bottom", "depth"} <= fields, sorted(fields)

    @staticmethod
    def _mixed_part():
        """Through and blind holes of one diameter, INTERLEAVED at one pitch on one axis.

        Interleaving is the point. Six identical through holes cannot mix by construction, and
        three-through-then-three-blind split positionally at the gap regardless of the spec key —
        both earlier versions of this fixture were tautologies. Here only the spec key separates
        them: neutering it yields one `LinearArray` of six with `{'through', 'flat'}`, measured.
        """
        from build123d import Align, Box, Cylinder, Pos

        centre = (Align.CENTER, Align.CENTER, Align.CENTER)
        part = Box(160, 60, 20, align=centre)
        for i, x in enumerate((-60, -40, -20, 0, 20, 40)):
            if i % 2 == 0:
                part -= Pos(x, 0, 0) * Cylinder(4, 60, align=centre)  # through
            else:
                part -= Pos(x, 0, 10) * Cylinder(4, 20, align=centre)  # blind
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
        # `>= 2` says only that the part still recognises as patterns — it carries NO
        # information about `bottom`, since these six also split positionally at the 40 mm gap.
        # The load-bearing assertion is `len(bottoms) == 1` inside the loop (#1229 review r2).
        assert len(groups) >= 2, (
            f"the mixed part produced {len(groups)} group(s); it stopped recognising"
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
            "importing draftwright.evaluation.step_analysis now pulls build123d, which costs "
            "one to two seconds. Its engine imports are deliberately inside function bodies "
            "(#313); see that module's docstring for the shape (#1229)."
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
            # `b123d_recognisers` too: measured, importing it puts build123d in `sys.modules`,
            # so it carries the same cost the note is about and the guard missed it entirely
            # (#1229 review round 3).
            if (isinstance(node, ast.ImportFrom) and (node.module or "").startswith(_ENGINE))
            or (
                isinstance(node, ast.Import)
                and any(a.name.startswith(_ENGINE) for a in node.names)
            )
        ]
        assert not offenders, (
            f"module-level engine import at line(s) {offenders}; keep them in function bodies"
        )
