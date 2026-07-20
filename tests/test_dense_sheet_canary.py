"""Fast-tier dense-sheet canary for the #733 strip-pressure regression class (#737).

#733 (the #636 regression, PR #689 "height ladder joins the corridor solve") dropped the
step-height and overall-height **principal** dimensions on the NIST CTC-02/04 fixtures —
``placement_unsatisfiable`` **error** lint, *"front-view right strip full"* — and sat on
``main`` for 12 hours because only the **slow** CTC fixtures exercise real strip pressure,
and the slow tier runs post-merge only (#153). The PR gate never saw it.

PR #734 fixed it *by construction*: **decoration places after the drain, so principal
dims win** — on an over-full front-view right strip the height/step chain is placed first
and the decoration (pocket/fillet dims) is what gets shed, never a principal.

This is the fast-tier tripwire for that guarantee. The synthetic part below double-loads
the same strip the CTC cases do: a four-step tower stacks a step-height chain + overall
height on the front-view right strip, and blind pockets milled into the **front face** put
competing pocket callouts on the same view. Pre-#734 the register-then-drain ordering let
an immediate pocket callout claim strip room a principal needed, so a step-height dim was
dropped (``placement_unsatisfiable``); post-#734 the principals drain and place first, so
every principal survives and the sheet stays free of error-severity lint. Build time is a
couple of seconds, so it runs on every PR.

It is a genuine regression test, empirically calibrated against the fix, not a smoke test:
run against the pre-#734 commit ``190e9ba`` (#636/PR #689) this exact part drops a
step-height dimension — ``placement_unsatisfiable`` "front-view right strip full", the #733
error — while on ``main`` (post-#734) every principal survives and only pocket decoration
is shed. So a revert of #734's drain-ordering (or any change that lets decoration outrank a
principal on a full strip) trips this test on the PR gate instead of post-merge.

Scope (ADR 0014; #735 — test-only, no solve rewrite): the slow CTC pair + golden corpus
remain the authoritative oracle; this narrows the *fast-tier* gap #733 fell through. The
build is a pure function of its input (guarded by ``test_layout_cleanliness``) and fonts
are path-pinned (ADR 0006), so the placed set is deterministic across the CI matrix.
"""

from __future__ import annotations

import pytest
from build123d import Box, Pos

from draftwright import build_drawing

_N_POCKETS = 8


def _contended_front_strip_part():
    """A part whose front-view right strip is contended: a step-height chain + overall
    height (the principals) competing with front-face pocket callouts (decoration).

    The four-step tower stacks the height chain on the front-right strip; the blind pockets
    milled into the front face (``-Y``) add competing pocket callouts to the same view.
    Pre-#734 the greedy register-then-drain ordering let a pocket callout claim strip room a
    principal needed, dropping a step-height dim; post-#734 the principals place first and
    all survive (#734: principals win by construction)."""
    part = Box(60, 40, 60)
    z, w = 60, 50
    for _ in range(4):  # step tower → the front-right step-height chain
        part += Pos(0, 0, z + 3) * Box(w, 40, 6)
        z += 6
        w -= 9
    zs = [6 + i * (48 / (_N_POCKETS - 1)) for i in range(_N_POCKETS)]
    for i, zz in enumerate(zs):  # blind pockets in the front face → competing decoration
        x = -18 if i % 2 == 0 else 18
        part -= Pos(x, -20, zz) * Box(9, 10, 4)
    return part


@pytest.mark.timeout(120)
def test_full_front_strip_sheds_decoration_not_principal_dims():
    # The #733 canary — the #734 "principals win by construction" guarantee, in the fast
    # tier. Empirically calibrated: this exact part drops a step-height dim
    # (placement_unsatisfiable, "front-view right strip full") on the pre-#734 commit
    # 190e9ba, and is clean here on main.
    dwg = build_drawing(_contended_front_strip_part())
    anns = set(dwg.annotations())

    # 1. No error-severity lint — directly catches the #733 `placement_unsatisfiable`
    #    "front-view right strip full" drop.
    errors = [(i.code, i.message) for i in dwg.lint() if i.severity == "error"]
    assert not errors, f"a principal dim was dropped from the full strip (#733 class): {errors}"

    # 2. The principal chain is intact: overall height + the full step-height ladder, all
    #    on the front view. (The pre-#734 commit drops one of these — see the docstring.)
    assert "dim_height" in anns, "overall-height dim dropped (front-right strip regression)"
    steps = sorted(n for n in anns if n.startswith("dim_step_"))
    assert len(steps) >= 5, f"step-height chain thinned out — expected >=5, got {steps}"
    assert all(dwg.view_of(n) == "front" for n in ["dim_height", *steps])

    # 3. Non-vacuous: the competing pocket decoration really is present, and on the SAME
    #    (front) view as the height chain — so guards 1–2 aren't trivially met by a part
    #    with no strip contention. (The decoration is not itself shed on main; the pre-#734
    #    ordering is what dropped a principal — see the docstring.) Fails closed: a future
    #    geometry change that removes the competing load would quietly turn this back into a
    #    smoke test, and this guard trips instead.
    pockets = [n for n in anns if n.startswith("m_pocket")]
    assert pockets, "no competing pocket decoration placed — the front strip is not contended"
    assert all(dwg.view_of(n) == "front" for n in pockets), (
        f"pocket decoration landed off the front view — not contending the height chain: "
        f"{[(n, dwg.view_of(n)) for n in pockets]}"
    )
