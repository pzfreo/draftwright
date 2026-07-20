"""Fast-tier dense-sheet canary for the #733 strip-pressure regression class (#737).

#733 (the "height ladder joins the corridor solve" regression, #636/PR #689) dropped
the step-height and overall-height *principal* dimensions on the NIST CTC-02/04 fixtures
— ``placement_unsatisfiable`` **error** lint, "front-view right strip full" — and sat on
``main`` for 12 hours because only the **slow** CTC fixtures exercise real strip pressure
and the slow tier runs post-merge only (#153). The PR gate never saw it.

This is the fast-tier tripwire that closes that gap: one synthetic part that builds in a
couple of seconds yet crowds the same two strips the CTC cases do —

  * the **front-view right strip**, with a stacked step-height chain + overall-height dim
    (``dim_height`` + ``dim_step_*``), the exact occupants #733 dropped; and
  * the **below strip**, with a row of vertical holes → location dimensions under the plan
    view (``m_locx*``).

It builds *clean* today (no error-severity lint), so a #636-class regression that tightens
the strip-capacity accounting and drops a principal dim trips it here, on the PR gate,
instead of post-merge.

Honesty note (ADR 0014; #735 scope — test-only, no solve rewrite): this is a *density*
canary, not a byte-for-byte reproduction of the CTC-02/04 capacity edge. It raises the
odds of catching the regression class in the fast tier; the slow CTC pair + golden corpus
remain the authoritative oracle (#735). The build is a pure function of its input (guarded
by ``test_layout_cleanliness``) and fonts are path-pinned (ADR 0006), so the placed set is
deterministic across the CI matrix.
"""

from __future__ import annotations

import pytest
from build123d import Box, Cylinder, Pos

from draftwright import build_drawing


def _dense_stepped_block():
    """A stepped prismatic block that double-loads the front-right and below strips.

    Six full-depth Z-steps (each 7 mm tall, well above the step-legibility floor at the
    auto scale so none collapse) stack a six-dimension height chain on the front view's
    right strip; a row of five through-holes drops five location dimensions under the plan
    view. Narrow in X/Y so the whole sheet stays at scale 1.0 — the pressure can't escape
    by rescaling, it has to be *placed*."""
    part = Box(70, 44, 6)  # base plate
    z, w = 6, 60
    for _ in range(6):  # 6 legible full-depth steps → the front-right step-height chain
        part += Pos(0, 0, z + 3.5) * Box(w, 44, 7)
        z += 7
        w -= 8
    for x in (-26, -13, 0, 13, 26):  # hole row → location dims below the plan view
        part -= Pos(x, 0, 0) * Cylinder(2.5, 200)
    return part


@pytest.mark.timeout(120)
def test_dense_sheet_places_every_principal_dim_no_error_lint():
    # The #733 canary. Two guards over the same build:
    #   1. NO error-severity lint — directly catches the `placement_unsatisfiable`
    #      "front-view right strip full" drop #733 produced.
    #   2. The principal chain is intact — `dim_height` (overall height) plus the full
    #      `dim_step_*` ladder on the front view, and every hole-row location dim under
    #      the plan. A regression that drops one of these fails here even if a future
    #      refactor were to record the drop at a non-error severity.
    dwg = build_drawing(_dense_stepped_block())
    anns = set(dwg.annotations())

    errors = [(i.code, i.message) for i in dwg.lint() if i.severity == "error"]
    assert not errors, f"dense sheet produced error lint (the #733 class): {errors}"

    # Front-right strip: overall height + the stacked step-height chain, all on the front.
    assert "dim_height" in anns, "overall-height dim dropped (front-right strip regression)"
    steps = sorted(n for n in anns if n.startswith("dim_step_"))
    assert len(steps) >= 5, f"step-height chain thinned out — expected >=5, got {steps}"
    assert all(dwg.view_of(n) == "front" for n in ["dim_height", *steps])

    # Below strip: the hole-row location dimensions under the plan view.
    locs = sorted(n for n in anns if n.startswith("m_locx"))
    assert len(locs) == 5, f"below-strip location dims dropped — expected 5, got {locs}"
    assert all(dwg.view_of(n) == "plan" for n in locs)
