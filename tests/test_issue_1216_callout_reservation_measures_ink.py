"""The callout-width reservation must measure the string the callout will DRAW.

ADR 2 (was 0014 Amendment 3): budgets must measure, not predict. `compose._est_planned_bore_callout_width`
sizes the page and the side strips from the planned IR before any annotation exists, and the
renderer then draws from the same plan — so the two are a measurement and its subject, and they
have drifted twice.

The second drift is what this file guards. When #1234 threaded authored tolerances into the
compound hole callout, the estimator kept measuring the BARE recess terms: a counterbored hole
with a toleranced counterbore was reserved the width of `⌀14` and drew `⌀14 ±0.1`, so the
placement check rejected a callout the reservation said would fit and dropped the whole
annotation with `callout_dropped: no room beside the view` — a wrong drawing produced from a
right one (#1234 review r7). The fix landed with no test; this is it (#1216 review r9).

The assertion is one-sided on purpose. An estimate BELOW the ink is the failure — it promises
room that is not there. An estimate above it is conservative, costs page, and is not a lie.
"""

from __future__ import annotations

import pytest
from build123d import Box, Cone, Cylinder, Pos

from draftwright.builder import build_drawing, detect_part_model
from draftwright.compose import _est_planned_bore_callout_width
from draftwright.model.callout import hole_callout_spec
from draftwright.model.planner import plan_dimensions

_TOL = 0.05


def _recessed_plate():
    """One counterbored and one countersunk through hole — every recess term at once."""
    part = Box(140, 60, 12)
    part -= Pos(-35, 0, 0) * Cylinder(4, 40)
    part -= Pos(-35, 0, 3) * Cylinder(7, 6)
    part -= Pos(35, 0, 0) * Cylinder(3, 40)
    part -= Pos(35, 0, 4) * Cone(3, 7, 4)
    return part


def _blind_plate():
    part = Box(80, 60, 12)
    for x in (-20, 20):
        part -= Pos(x, 0, 5) * Cylinder(5, 4)
    return part


@pytest.mark.parametrize(
    "part_fn",
    [_recessed_plate, _blind_plate],
    ids=["recessed", "blind"],
)
def test_direct_detection_matches_built_model_features(part_fn):
    """The model-only fast path must preserve both geometries used by this module."""
    built = tuple(build_drawing(part_fn(), title="T", number="N").model().features)
    detected = tuple(detect_part_model(part_fn()).features)
    assert detected == built


def _widest_drawn_callout(drawing) -> float:
    """The widest bore-callout LABEL actually on the sheet, in page mm."""
    widths = []
    for name in drawing.registry.names():
        if not name.startswith("hc_"):
            continue
        box = drawing.registry.named(name).label_bbox
        widths.append(float(box[2] - box[0]))
    return max(widths, default=0.0)


def _decorations(model, *, kinds):
    """Every hole parameter of these kinds, toleranced — the bare `(feature, kind)` key.

    That key is the public spelling, and it is the one that tolerances a counterbore's
    diameter alongside its bore's, which is the case the estimator got wrong.
    """
    return {
        (feature, kind): _TOL
        for feature in model.features
        if feature.kind == "hole"
        for kind in kinds
        if any(p.kind == kind for p in feature.parameters())
    }


@pytest.mark.parametrize(
    ("name", "part_fn", "kinds"),
    [
        ("counterbore_and_countersink", _recessed_plate, ("diameter",)),
        ("counterbore_depths", _recessed_plate, ("diameter", "length", "angle")),
        ("blind_depth", _blind_plate, ("diameter", "length")),
    ],
)
def test_the_reservation_is_never_narrower_than_the_ink(name, part_fn, kinds):
    model = detect_part_model(part_fn())
    decorations = _decorations(model, kinds=kinds)
    assert decorations, f"{name}: precondition — this part has no parameter of {kinds}"

    drawing = build_drawing(part_fn(), model=model, decorations=decorations, title="T", number="N")
    drawn = _widest_drawn_callout(drawing)
    assert drawn > 0.0, f"{name}: precondition — no bore callout reached the sheet"

    # The precondition that matters: a tolerance is actually IN the widest label, so this is
    # measuring the toleranced string and not the bare one it would have measured anyway.
    labels = [
        str(drawing.registry.named(n).label or "")
        for n in drawing.registry.names()
        if n.startswith("hc_")
    ]
    assert any("±" in label for label in labels), (
        f"{name}: precondition — no callout renders a tolerance, so the bare and toleranced "
        f"estimates cannot differ: {labels}"
    )

    estimate = _est_planned_bore_callout_width(plan_dimensions(drawing.model()), drawing.draft)
    assert estimate >= drawn, (
        f"{name}: the page was sized for {estimate:.2f} mm of callout and {drawn:.2f} mm was "
        f"drawn, so the reservation under-promises by {drawn - estimate:.2f} mm. Every term the "
        "callout prints must be measured with its tolerance — see `_term` in "
        "`compose._est_planned_bore_callout_width`."
    )


def test_a_recess_tolerance_alone_widens_the_reservation():
    """The control for `_term`, which is the line that was wrong.

    Its first version toleranced every `diameter`, which includes the BORE — and the bore's
    suffix is applied on its own line (`_est_planned_bore_callout_width`, the `token_w.append`
    above `_term`), so the estimate moved whether or not `_term` read anything. Measured:
    making `_term` bare left that control PASSING and only the main assertions failing, so the
    "control" guarded nothing it claimed to (#1216 review r9, F9).

    Toleranced here through the ROLE key, on the counterbore's diameter alone, so the bore is
    bare and any movement must come from `_term`.
    """
    model = detect_part_model(_recessed_plate())
    recess = {
        (feature, param.kind, param.role): _TOL
        for feature in model.features
        if feature.kind == "hole"
        for param in feature.parameters()
        if param.role in {"counterbore", "countersink"}
    }
    assert recess, "precondition: this part has no recess parameter to tolerance"

    bare = build_drawing(_recessed_plate(), model=model, title="T", number="N")
    toleranced = build_drawing(
        _recessed_plate(), model=model, decorations=recess, title="T", number="N"
    )
    # The bore itself must stay bare, or this is the old control again. Read off the spec the
    # estimator and the renderer both consume, which is where `tolerance` (the bore's, applied
    # on its own line) and the per-term `*_tol` keys are separated.
    specs = [
        hole_callout_spec(group)
        for group in plan_dimensions(toleranced.model())
        if hole_callout_spec(group) is not None
    ]
    assert specs, "precondition: no hole callout spec to measure"
    assert all(spec.get("tolerance") is None for spec in specs), (
        "precondition: the bore is toleranced too, so `_term` is not isolated: "
        f"{[spec.get('tolerance') for spec in specs]}"
    )
    assert any(
        spec.get(key) is not None
        for spec in specs
        for key in ("cbore_dia_tol", "cbore_depth_tol", "csink_dia_tol", "csink_angle_tol")
    ), "precondition: no recess term carries a tolerance, so there is nothing for `_term` to read"

    bare_estimate = _est_planned_bore_callout_width(plan_dimensions(bare.model()), bare.draft)
    tol_estimate = _est_planned_bore_callout_width(
        plan_dimensions(toleranced.model()), toleranced.draft
    )
    assert tol_estimate > bare_estimate, (
        f"a recess tolerance widened no reservation ({bare_estimate:.2f} -> "
        f"{tol_estimate:.2f} mm), so `_term` is not reading the per-term tolerances"
    )
