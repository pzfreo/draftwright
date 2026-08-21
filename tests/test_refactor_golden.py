"""Strict placement golden for the #638/#639 hotspot-split refactors (epic #635).

These refactors (`render_pmi`, `_annotate_holes`, `finalize`, the #639 PlacementContext
threading) are **behaviour-preserving** — the output must not change at all. This gate is a
stronger, wider counterpart to the retired byte-exact golden (ADR 0007) and the since-retired
ADR-0009 `test_layout_snapshot` (#641 gap 3): it snapshots the FULL placement signature of a
corpus chosen to
exercise every path the split touches — machined-feature leader callouts (chamfer/fillet/
flat/pocket/groove), off-axis hole locations, prismatic height ladders + step positions,
sections, turned diameters, dense-hole table escalation — and also the **build-issue set**
(drops/escalations), which a placement-only snapshot misses but the drop logic in
`_annotate_holes`/`render_pmi` is load-bearing on.

Precision: the 0.1 mm + 1e-6-bias quantisation proven cross-platform-stable for the ADR-0009
snapshot (ADR 0006 pinned fonts). A raw byte/SVG digest is deliberately NOT used — a refactor
that reorders floating-point sums shifts a value ~1 ULP with no real placement change, which a
byte digest false-fails on; 0.1 mm catches the ~mm drift a wrong projector/sign/order causes
while surviving that noise.

## What this corpus asserts, and why it changed (#1130)

It was a byte-exact deviation gate: `signature == recorded signature`, failing with "this is
a real regression, NOT to be re-blessed away". It said, in its own header, that it was
retained "until that epic concludes" — naming #602, #638 and #639. All three closed
2026-07-17, and #740 on 2026-08-15. The condition expired and the gate was carried on
unexamined into ADR 0018, whose entire purpose is to change the coordinates it pinned.

That is the wrong instrument pointed the wrong way. A standing byte-exact assertion on a
layout the engine is being taught to IMPROVE fails every improvement — each arriving framed
as a regression needing a defence — and passes everything that improves nothing. It biases
the work toward changes that change nothing, which is measurably what happened.

So the standing contract is now what this corpus was always really for — what a drawing must
not LOSE (see `_regressions`):

- no annotation vanishes, and a retained one keeps its label (ADR 0016 Amdt 6);
- no build issue gets worse;
- the sheet never grows — a ratchet against the baseline, so footprint may shrink freely and
  may never creep back.

A smaller sheet that loses nothing now PASSES, silently and correctly. A lost dimension, a
new drop, or a bigger sheet still fails. That is the regression class that matters: it is
what caught `centered_rebate` and `scattered_plate` losing dimensions when the ADR 0018
arrangement gate was being built.

Byte-exact is not gone, it is opt-in — run it deliberately for a change that CLAIMS to alter
nothing, which is exactly the refactor case the gate was created for:

    DRAFTWRIGHT_GOLDEN_EXACT=1 uv run pytest tests/test_refactor_golden.py

#1250 re-baselined `grooved_shaft`, `grid_plate` and `scattered_plate`: each carries a required placement failure
that was reported below error severity, so the automatic path returned them as successes. The
build now records a `plan_incomplete` error summarising the loss. Checked against this
corpus's own standard before re-recording — annotations identical, labels identical, page
identical, one build issue ADDED and none removed. The drawings did not get worse; the report
got honest, which is the one direction a completeness gate must not mistake for a regression.

Re-bless the recorded baseline (advances the footprint ratchet — do it deliberately):
    DRAFTWRIGHT_UPDATE_GOLDEN=1 uv run pytest tests/test_refactor_golden.py
"""

from __future__ import annotations

import collections
import json
import os
from pathlib import Path

import pytest
from build123d import Axis, Box, Cylinder, Pos, Rot, Rotation, chamfer, fillet

from draftwright import build_drawing

_GOLDEN_DIR = Path(__file__).parent / "refactor_golden"


# --- corpus: fast parts exercising the #638/#639 code paths ------------------------------


def _chamfered():
    # A single corner chamfer → render_chamfers leader callout.
    plate = Box(90, 60, 20)
    e = plate.edges().filter_by(Axis.Z).sort_by(lambda e: e.center().X + e.center().Y)[-1]
    return chamfer(e, 12)


def _filleted():
    # A single corner fillet → render_fillets leader callout.
    plate = Box(90, 60, 20)
    e = plate.edges().filter_by(Axis.Z).sort_by(lambda e: e.center().X + e.center().Y)[-1]
    return fillet(e, 8)


def _hex_bar():
    # Six flats 60° apart on round stock → render_flats "A/F" callout.
    bar = Cylinder(10, 30)
    for k in range(6):
        bar = bar - Rot(0, 0, 60 * k) * Pos(10.3, 0, 0) * Box(2, 40, 40)
    return bar


def _grooved_shaft():
    # Two annular grooves on one shaft → render_grooves callouts + turned diameters/lengths.
    shaft = Cylinder(10, 60)
    shaft -= Pos(0, 0, 15) * (Cylinder(10, 4) - Cylinder(8, 4))
    shaft -= Pos(0, 0, -15) * (Cylinder(10, 4) - Cylinder(7, 4))
    return shaft


def _pocketed():
    # A blind pocket + a through hole → render_pockets callout + _annotate_holes.
    return Box(80, 60, 20) - Pos(0, 10, 5) * Box(30, 20, 20) - Pos(-20, -15, 0) * Cylinder(3, 30)


def _side_drilled():
    # Radial (X-axis) through-holes at two heights → _locate_off_axis_holes (side/below).
    part = Box(60, 40, 30)
    for z in (8, 20):
        part -= Pos(0, 0, z) * Rotation(0, 90, 0) * Cylinder(3, 80)
    return part


def _prismatic_ladder():
    # An asymmetric step → height ladder + a step-position (shoulder) dim.
    return Box(80, 60, 30) - Pos(0, -20, 7.5) * Box(80, 20, 15)


def _centered_rebate():
    # A central channel → two shoulders, both positions dimensioned.
    return Box(80, 60, 30) - Pos(0, 0, 7.5) * Box(80, 20, 15)


def _bracket_section():
    # Central bore + offset counterbore → plan callouts + section A-A.
    return Box(90, 60, 20) - Cylinder(4, 20) - Pos(10, 5, -7) * Cylinder(6, 6)


def _turned_stepped():
    # Z-turned stepped cylinder → step diameters + axial length chain.
    from build123d import Align

    base = (Align.CENTER, Align.CENTER, Align.MIN)
    s = Cylinder(12, 16, align=base)
    s += Pos(0, 0, 16) * Cylinder(8, 14, align=base)
    s += Pos(0, 0, 30) * Cylinder(5, 10, align=base)
    return s


def _flange_dense():
    # A bolt circle → dense plan holes escalate to the hole TABLE + balloon ring.
    import math

    flange = Cylinder(radius=45, height=10) - Cylinder(radius=8, height=10)
    for i in range(5):
        ang = math.radians(72 * i)
        flange -= Pos(30 * math.cos(ang), 30 * math.sin(ang), 0) * Cylinder(3, 10)
    return flange


def _grid_plate():
    # The #602 benchmark: a plate with 15 holes in two regular grid patterns → grid pitch
    # dims through _place_pitch_dim's bounded-offset fallback (the perf hotspot: the strip
    # carve finds no free tier, so every candidate offset is footprint-tested).
    plate = Box(120, 80, 10)
    for i in range(3):
        for j in range(3):
            plate -= Pos(-45 + i * 15, -15 + j * 15, 0) * Cylinder(2.5, 10)
    for i in range(2):
        for j in range(3):
            plate -= Pos(25 + i * 20, -20 + j * 18, 0) * Cylinder(4, 10)
    return plate


def _scattered_plate():
    # The #602 second benchmark: ten holes, four diameters, no regular pattern → a large
    # corridor-candidate set through place_strip_candidates (the measure/build seam).
    plate = Box(140, 90, 12)
    spots = [
        (-55, -30, 3),
        (-40, 25, 4),
        (-20, -10, 2.5),
        (-5, 35, 5),
        (10, -35, 3),
        (25, 10, 4),
        (40, -20, 2.5),
        (55, 30, 5),
        (60, -38, 3),
        (-60, 38, 4),
    ]
    for x, y, r in spots:
        plate -= Pos(x, y, 0) * Cylinder(r, 12)
    return plate


def _holed_slot():
    # A hole whose X-location coincides with a slot edge → the #345 corridor dedup path.
    from build123d import BuildPart, Hole, Locations, Mode

    with BuildPart() as p:
        Box(60, 40, 20)
        Box(20, 8, 30, mode=Mode.SUBTRACT)
        with Locations((-10, 14, 0), (20, 14, 0), (8, -14, 0)):
            Hole(3, depth=20)
    return p.part


CORPUS = {
    "chamfered": _chamfered,
    "filleted": _filleted,
    "hex_bar": _hex_bar,
    "grooved_shaft": _grooved_shaft,
    "pocketed": _pocketed,
    "side_drilled": _side_drilled,
    "prismatic_ladder": _prismatic_ladder,
    "centered_rebate": _centered_rebate,
    "bracket_section": _bracket_section,
    "turned_stepped": _turned_stepped,
    "flange_dense": _flange_dense,
    "grid_plate": _grid_plate,
    "scattered_plate": _scattered_plate,
    "holed_slot": _holed_slot,
}


# --- signature ---------------------------------------------------------------------------


def _round_bbox(box):
    # 0.1 mm grid + 1e-6 bias: stable under a refactor's FP-reordering, sensitive to real drift.
    if box is None:
        return None
    return [round(float(v) + 1e-6, 1) for v in box]


def _geom_box(o):
    try:
        b = o.bounding_box()
        return (b.min.X, b.min.Y, b.max.X, b.max.Y)
    except Exception:
        return None


def _signature(dwg) -> dict:
    annotations = sorted(
        (
            {
                "name": name,
                "view": dwg.view_of(name),
                "type": type(o).__name__,
                "label": getattr(o, "label", "") or "",
                "label_bbox": _round_bbox(getattr(o, "label_bbox", None)),
                "geom_bbox": _round_bbox(_geom_box(o)),
            }
            for name, o in dwg.iter_annotations()
        ),
        key=lambda a: a["name"],
    )
    views = {}
    for vname, shapes in dwg.views.items():
        vis = shapes[0] if isinstance(shapes, (tuple, list)) else shapes
        views[vname] = _round_bbox(_geom_box(vis))
    # Build issues (drops / escalations / warnings) — the drop logic in _annotate_holes and
    # render_pmi is load-bearing and a placement-only snapshot would miss a change to it.
    # Lists, not tuples: JSON has no tuple type, so tuples would fail the round-trip compare.
    issues = sorted([i.severity, i.code, i.message] for i in dwg.registry.issues)
    return {
        # The selected sheet — the footprint half of the contract below. Positions are still
        # recorded (they are what EXACT mode compares) but are no longer asserted by default.
        "page": [dwg.page_w, dwg.page_h],
        "views": views,
        "annotations": annotations,
        "item_count": len(dwg.items),
        "build_issues": issues,
    }


def _regressions(expected: dict, sig: dict) -> list[str]:
    """What this corpus now polices: nothing lost, nothing worse, nothing bigger.

    Three checks, each naming a class of real defect rather than a coordinate:

    1. **No annotation vanishes, and a retained one keeps its label.** The direct expression
       of ADR 0016 Amdt 6 — a dimension the plan approved must not silently disappear.
       Additions are NOT flagged: more of the part being dimensioned is the goal.
    2. **No new build issue.** The drop/escalation logic is load-bearing, and a snapshot that
       missed a change to it would miss the thing most worth catching.
    3. **The sheet does not grow.** A ratchet against the recorded baseline rather than a
       pin: the footprint may shrink freely and forever, and may never creep back.
    """
    problems: list[str] = []

    old_ann = {a["name"]: a for a in expected["annotations"]}
    new_ann = {a["name"]: a for a in sig["annotations"]}
    for name in sorted(set(old_ann) - set(new_ann)):
        problems.append(f"annotation LOST: {name} (label {old_ann[name]['label']!r})")
    for name in sorted(set(old_ann) & set(new_ann)):
        was, now = old_ann[name]["label"], new_ann[name]["label"]
        if was != now:
            problems.append(f"label CHANGED on {name}: {was!r} -> {now!r}")

    old_codes = collections.Counter(code for _sev, code, _msg in expected["build_issues"])
    new_codes = collections.Counter(code for _sev, code, _msg in sig["build_issues"])
    for code in sorted(new_codes):
        if new_codes[code] > old_codes.get(code, 0):
            problems.append(
                f"build issue WORSE: {code} x{new_codes[code]} (was x{old_codes.get(code, 0)})"
            )

    was_page, now_page = expected.get("page"), sig.get("page")
    if was_page and now_page and (now_page[0] * now_page[1]) > (was_page[0] * was_page[1]) + 1.0:
        problems.append(
            f"sheet GREW: {now_page[0]:.0f}x{now_page[1]:.0f} was {was_page[0]:.0f}x{was_page[1]:.0f}"
        )
    return problems


@pytest.mark.parametrize("name", list(CORPUS))
def test_refactor_golden(name):
    dwg = build_drawing(CORPUS[name]())
    sig = _signature(dwg)
    golden = _GOLDEN_DIR / f"{name}.json"

    if os.environ.get("DRAFTWRIGHT_UPDATE_GOLDEN"):
        _GOLDEN_DIR.mkdir(exist_ok=True)
        golden.write_text(json.dumps(sig, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return

    assert golden.exists(), (
        f"no golden for {name!r}; generate with "
        f"DRAFTWRIGHT_UPDATE_GOLDEN=1 uv run pytest tests/test_refactor_golden.py"
    )
    expected = json.loads(golden.read_text(encoding="utf-8"))

    if os.environ.get("DRAFTWRIGHT_GOLDEN_EXACT"):
        # Opt-in byte-exact mode — the original contract, for a change that CLAIMS to alter
        # nothing. Run it deliberately when refactoring; it is not the standing gate, because
        # a standing byte-exact gate on a layout the engine is meant to improve fails every
        # improvement and passes everything that improves nothing.
        assert sig == expected, (
            f"placement/issue drift for {name!r} under DRAFTWRIGHT_GOLDEN_EXACT. A change "
            f"that claims to be behaviour-preserving moved something."
        )
        return

    problems = _regressions(expected, sig)
    assert problems == [], (
        f"regression for {name!r} — this corpus polices what a drawing must not lose, not "
        f"where it puts things:\n  " + "\n  ".join(problems)
    )


# --- the contract itself, exercised directly -------------------------------------------
#
# A gate that cannot fail is worse than no gate, and this one now decides what the whole
# ADR 0018 epic is allowed to change. Each check is driven from a crafted baseline rather
# than a built drawing, so a defect in `_regressions` cannot hide behind a part that happens
# not to exhibit it.

_BASE = {
    "page": [420.0, 297.0],
    "annotations": [
        {"name": "dim_width", "label": "80"},
        {"name": "dim_height", "label": "20"},
    ],
    "build_issues": [["warning", "step_dim_withheld", "..."]],
}


def _sig(**overrides):
    return {**_BASE, **overrides}


class TestTheRegressionContract:
    def test_an_unchanged_drawing_is_clean(self):
        # The precondition for every case below meaning anything.
        assert _regressions(_BASE, _sig()) == []

    def test_a_lost_annotation_fails(self):
        gone = _sig(annotations=[{"name": "dim_width", "label": "80"}])
        assert [p for p in _regressions(_BASE, gone) if "LOST" in p]

    def test_a_changed_label_fails(self):
        relabelled = _sig(
            annotations=[
                {"name": "dim_width", "label": "80"},
                {"name": "dim_height", "label": "20.5"},
            ]
        )
        assert [p for p in _regressions(_BASE, relabelled) if "CHANGED" in p]

    def test_a_new_build_issue_fails(self):
        worse = _sig(
            build_issues=[
                ["warning", "step_dim_withheld", "..."],
                ["error", "location_ref_dropped", "..."],
            ]
        )
        assert [p for p in _regressions(_BASE, worse) if "WORSE" in p]

    def test_more_of_an_existing_issue_fails(self):
        # Counted, not merely set-membership: two drops where there was one is a regression.
        twice = _sig(
            build_issues=[
                ["warning", "step_dim_withheld", "..."],
                ["warning", "step_dim_withheld", "..."],
            ]
        )
        assert [p for p in _regressions(_BASE, twice) if "WORSE" in p]

    def test_a_bigger_sheet_fails(self):
        assert [p for p in _regressions(_BASE, _sig(page=[594.0, 420.0])) if "GREW" in p]

    def test_a_smaller_sheet_passes(self):
        # The whole point of the change: an improvement must land without an argument.
        assert _regressions(_BASE, _sig(page=[297.0, 210.0])) == []

    def test_an_added_annotation_passes(self):
        # More of the part dimensioned is the goal, not a deviation to be defended.
        more = _sig(annotations=[*_BASE["annotations"], {"name": "dim_depth", "label": "30"}])
        assert _regressions(_BASE, more) == []

    def test_a_moved_annotation_passes(self):
        # Position is what this gate deliberately stopped asserting; `test_layout_cleanliness`
        # and the layout fuzzers own collision-free/in-bounds, which is the real property.
        moved = _sig(
            annotations=[
                {"name": "dim_width", "label": "80", "geom_bbox": [999, 999, 1000, 1000]},
                {"name": "dim_height", "label": "20"},
            ]
        )
        assert _regressions(_BASE, moved) == []
