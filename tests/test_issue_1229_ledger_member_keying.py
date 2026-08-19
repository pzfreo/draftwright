"""#1229 — three seams in the hole-requirement ledger's member keying.

All three were latent: none produced a wrong answer on any fixture. They are worth closing
anyway because each is a place where the ledger's own join contract is stated in one place and
quietly not honoured in another, which is how #1223's real defect (a consumer keying a different
axis space from the ledger) got in.

1. `hole_requirement_outcomes` builds `HoleRequirementOutcome` at three sites. Two passed
   `members`; the `unmatched_countersinks` tail did not, so those outcomes carried the `()`
   default and no site-keyed consumer could reach them — `canonical_hole_sites` would find no
   match and fall through to its "unknown" default. That is the silent join failure the helper
   exists to prevent, one branch below where it is enforced.

2. `_members`' grouped branch decided `through` with `all(...)` over the members while the
   single-record branch decided it per hole. Equivalent in fact, misleading as code — see
   `TestAGroupCannotMixThroughAndBlind`, which measures the invariant that makes it so.

3. The module-level imports in `evaluation/step_analysis.py`. Filed as "unexplained"; measuring
   showed the filing was wrong — see that module's docstring, and
   `TestTheEvaluationModuleStaysCheapToImport` here.

The first guard is structural rather than behavioural on purpose. The countersink site is
unreachable through the public API — it needs a part whose recognised countersinks outnumber the
single `HoleRecord.csink` slot, and a plate countersunk on both faces does not do it (both seats
are recognised, neither attaches, and `countersink_matches_hole` rejects both). A test that
cannot reach the branch cannot pin it; a test that reads the source can, and it covers the two
sites that were already correct plus any site added later.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import subprocess
import sys
from pathlib import Path

from b123d_recognisers import HoleSpec

from draftwright.linting import hole_coverage

_SOURCE = Path(inspect.getfile(hole_coverage))


class TestEveryOutcomeSiteKeysItsMembers:
    """The ledger is joined by member site. An outcome with none cannot be joined at all."""

    def _construction_sites(self) -> list[ast.Call]:
        tree = ast.parse(_SOURCE.read_text())
        return [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "HoleRequirementOutcome"
        ]

    def test_the_source_really_contains_several_sites(self):
        # Precondition. An AST guard that matches nothing is a broken harness reporting success.
        sites = self._construction_sites()
        assert len(sites) >= 3, f"found {len(sites)} construction sites; the guard is not looking"

    def test_every_site_passes_members(self):
        missing = [
            node.lineno
            for node in self._construction_sites()
            if not any(kw.arg == "members" for kw in node.keywords)
        ]
        assert not missing, (
            f"{_SOURCE.name} lines {missing} build a HoleRequirementOutcome without `members`. "
            "A site-keyed consumer cannot reach it, so it falls through `canonical_hole_sites` "
            "to an 'unknown' default — the silent join failure that helper exists to prevent "
            "(#1229). Pass the outcome's own site."
        )

    def test_the_countersink_tail_keys_its_seat(self):
        # The site that was wrong, named directly, so a future edit that drops it again fails
        # here with the reason rather than only on the generic guard above.
        source = _SOURCE.read_text()
        tail = source[source.index("for countersink in unmatched_countersinks:") :]
        call = tail[: tail.index("return outcomes")]
        assert "members=(at,)" in call, (
            "the unmatched-countersink outcomes must carry their seat's location; "
            "'unverifiable' means unjoinable to IR provenance, not position-less"
        )


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

    def test_every_recognised_group_is_uniform_in_bottom(self):
        from build123d import Align, Box, Cylinder, Pos

        from draftwright.builder import build_drawing

        centre = (Align.CENTER, Align.CENTER, Align.CENTER)
        part = Box(120, 80, 12, align=centre)
        for x, y in ((-40, -20), (0, -20), (40, -20), (-40, 20), (0, 20), (40, 20)):
            part -= Pos(x, y, 0) * Cylinder(4, 40, align=centre)
        drawing = build_drawing(part, title="T", number="N-1")
        recognition = drawing.recognition()
        groups = list(getattr(recognition, "hole_patterns", ()))
        assert groups, "no hole pattern recognised; the assertion below is vacuous"
        checked = 0
        for group in groups:
            bottoms = {HoleSpec.from_hole(h).bottom for h in group.holes}
            checked += len(group.holes)
            assert len(bottoms) == 1, (group, bottoms)
        assert checked >= 3, f"only {checked} grouped holes examined"


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
        offenders = [
            node.lineno
            for node in tree.body
            if isinstance(node, ast.ImportFrom)
            and (node.module or "").startswith(("draftwright", "build123d"))
        ]
        assert not offenders, (
            f"module-level engine import at line(s) {offenders}; keep them in function bodies"
        )
